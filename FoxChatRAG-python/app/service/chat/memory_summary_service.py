"""
记忆总结服务模块

职责：
- 6轮对话后的消息总结（存入向量数据库）
- 从对话中提取关键事件（存入 Memory Bank）
- Memory Bank 压缩（超过阈值时触发）
- 用户画像更新（在总结流程中调用）

阶段3升级：
- 引入候选分流：A2 / 当前状态 / 时间节点 / 历史事件 四路候选
- 使用候选路由服务进行分流和无变化判断
- 各路候选分别写回对应存储
"""

import json
import re
from datetime import datetime
from typing import List

from loguru import logger

from app.common.constant.ChromaTypeConstant import ChromaTypeConstant
from app.common.constant.FileTypeConstant import FileTypeConstant
from app.common.constant.LLMChatConstant import LLMChatConstant, build_memory_key
from app.core.db.redis_client import redis_client
from app.core.llm_model import model as llm_model
from app.core.prompts.prompt_manager import PromptManager
from app.core.prompts.prompt_template import PromptTemplate
from app.util import loader_util, chroma_util
from app.util.template_util import escape_template
from app.service.chat.user_profile_service import update_user_profile_in_summary
# 移除 candidate router 主路径依赖（simplify-memory-a2-profile）
# from app.service.chat.candidate_router_service import route_summary_candidates
# from app.schemas.summary_candidate import SummaryBatchResult

MEMORY_BANK_MAX_SIZE = 50
MEMORY_BANK_COMPRESS_TARGET = 30

RECENT_MSG_KEEP_SIZE = 10
SUMMARY_TRIGGER_THRESHOLD = 18


def _extract_json_array_text(raw_text: str) -> str:
    """从模型输出中提取 JSON 数组文本。"""
    if not raw_text:
        return ""

    text = raw_text.strip()
    text = re.sub(r"```(?:json)?", "", text)
    text = text.replace("```", "").strip()

    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return text
    return text[start:end + 1]


def _load_event_list(raw_text: str) -> List[dict]:
    """解析事件列表，兼容模型输出包裹文本。"""
    json_text = _extract_json_array_text(raw_text)
    events = json.loads(json_text)
    if not isinstance(events, list):
        raise json.JSONDecodeError("event payload is not a list", json_text, 0)
    return events


async def _build_summary_chain():
    """构建消息总结 Chain"""
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    template = ChatPromptTemplate(
        [
            ("system", PromptTemplate.SUMMARY_SYSTEM_PROMPT_TEMPLATE),
            ("human", "The chat history between the user and the role currently played by the AI is: {chat_history_msg}")
        ]
    )

    str_parser = StrOutputParser()
    llm = await llm_model.get_summary_model()
    chain = template | llm | str_parser
    return chain


async def _build_event_extractor_chain():
    """构建事件提取 Chain"""
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    prompt_str = await PromptManager.get_prompt("memory_event_extractor.md")
    prompt_str = escape_template(prompt_str, ["input_content"])
    template = ChatPromptTemplate([
        ("system", prompt_str),
        ("human", "{input_content}")
    ])

    str_parser = StrOutputParser()
    llm = await llm_model.get_extraction_model()
    chain = template | llm | str_parser
    return chain


async def _extract_memory_events(recent_msg_list: List[str]) -> List[dict]:
    """从对话历史中提取关键事件（兼容旧格式）"""
    if not recent_msg_list:
        return []

    chat_history = "\n".join(recent_msg_list)
    chain = await _build_event_extractor_chain()
    result = await chain.ainvoke({"input_content": chat_history})

    try:
        events = _load_event_list(result)
        current_time = datetime.now().strftime("%Y-%m-%d")
        for event in events:
            if "time" not in event or not event["time"]:
                event["time"] = current_time
            if "actor" not in event or not event["actor"]:
                event["actor"] = "UNKNOWN"
                logger.warning(f"事件缺少 actor 字段，已设置默认值: {event.get('content', '')}")
            if "keywords" not in event:
                event["keywords"] = []
        return events
    except json.JSONDecodeError as e:
        logger.warning(f"事件提取 JSON 解析失败: {e}; 原始输出: {result}")
        return []


async def _append_to_memory_bank(events: List[dict], user_id: str, llm_id: str) -> None:
    """将提取的事件追加到 Memory Bank"""
    if not events:
        return

    memory_bank_key = build_memory_key(LLMChatConstant.MEMORY_BANK, user_id, llm_id)

    existing = redis_client.get(memory_bank_key)
    if existing:
        try:
            memory_bank = json.loads(existing)
        except json.JSONDecodeError:
            memory_bank = []
    else:
        memory_bank = []

    memory_bank.extend(events)
    redis_client.set(memory_bank_key, json.dumps(memory_bank, ensure_ascii=False))
    logger.debug(f"已追加 {len(events)} 条事件到 memory_bank")


async def _compress_memory_bank_if_needed(user_id: str, llm_id: str) -> None:
    """检查并压缩 Memory Bank（超过阈值时）"""
    memory_bank_key = build_memory_key(LLMChatConstant.MEMORY_BANK, user_id, llm_id)

    existing = redis_client.get(memory_bank_key)
    if not existing:
        return

    try:
        memory_bank = json.loads(existing)
    except json.JSONDecodeError:
        return

    if len(memory_bank) < MEMORY_BANK_MAX_SIZE:
        return

    logger.info(f"memory_bank 长度 {len(memory_bank)} 超过阈值，开始压缩...")

    compress_prompt = f"""将以下记忆库压缩到 {MEMORY_BANK_COMPRESS_TARGET} 条核心事件。
    要求：
    - 合并相似事件
    - 保留最重要的关键事件
    - 保持 time、type、actor、content、keywords 字段
    - actor 字段必须保留，明确主体归属
    - keywords 字段合并相似关键词
    - 输出 JSON 数组格式
    - 只输出 JSON 数组，不要其他文字

    当前记忆库：
    {json.dumps(memory_bank, ensure_ascii=False, indent=2)}

    压缩后的记忆库：
    """

    llm = await llm_model.get_memory_model()
    compressed = await llm.ainvoke(compress_prompt)

    try:
        compressed_memory_bank = json.loads(compressed)
        redis_client.set(memory_bank_key, json.dumps(compressed_memory_bank, ensure_ascii=False))
        logger.info(f"memory_bank 压缩完成: {len(memory_bank)} -> {len(compressed_memory_bank)} 条")
    except json.JSONDecodeError:
        logger.warning(f"memory_bank 压缩 JSON 解析失败")


async def _summary_and_upload(recent_msg_list: List[str], user_id: str, llm_id: str) -> str:
    """总结消息并上传到向量数据库，返回总结文本"""
    chain = await _build_summary_chain()
    summary_msg = await chain.ainvoke({
        "chat_history_msg": recent_msg_list,
        "recent_msg_list": recent_msg_list
    })

    documents = loader_util.load_file(summary_msg, FileTypeConstant.STR)
    source_id = user_id + llm_id + summary_msg
    await chroma_util.upload(ChromaTypeConstant.CHAT, documents, source_id, user_id=user_id, llm_id=llm_id)

    return summary_msg


# 移除 candidate 分流主路径（simplify-memory-a2-profile）
# async def _process_candidates(
#     candidates: SummaryBatchResult,
#     user_id: str,
#     llm_id: str,
# ) -> None:
#     """
#     处理四路候选并写回
#
#     阶段3新增：分流后的候选分别写回对应存储
#     """
#     from app.service.chat.state_manager import update_current_state, get_current_state, get_current_round
#     from app.service.chat.time_node_service import create_time_node
#     from app.schemas.current_state import UpdateSource
#
#     current_round = get_current_round(user_id, llm_id)
#
#     # 1. A2 候选处理
#     await _process_a2_candidates(candidates.a2_candidates, user_id, llm_id)
#
#     # 2. 当前状态候选处理
#     await _process_current_state_candidates(
#         candidates.current_state_candidates,
#         user_id,
#         llm_id,
#         current_round,
#     )
#
#     # 3. 时间节点候选处理
#     await _process_time_node_candidates(
#         candidates.time_node_candidates,
#         user_id,
#         llm_id,
#     )
#
#     # 4. 历史事件候选处理（写入 memory_bank）
#     await _process_history_event_candidates(
#         candidates.history_event_candidates,
#         user_id,
#         llm_id,
#     )


# 移除 candidate 分流主路径（simplify-memory-a2-profile）
# async def _process_a2_candidates(
#     candidates: list,
#     user_id: str,
#     llm_id: str,
# ) -> None:
#     """处理 A2 候选"""
#     if not candidates:
#         return
#
#     # 阶段3第一版：A2 候先写入过渡存储
#     # 后续阶段会独立 A2 容器
#     a2_key = f"chat:memory:{user_id}:{llm_id}:a2_candidates"
#
#     existing = redis_client.get(a2_key)
#     if existing:
#         try:
#             existing_list = json.loads(existing)
#         except json.JSONDecodeError:
#             existing_list = []
#     else:
#         existing_list = []
#
#     for candidate in candidates:
#         if candidate.should_promote_immediately():
#             # 直接提升为活跃边界
#             logger.info(f"【A2提升】立即提升: {candidate.content[:30]}...")
#             existing_list.append({
#                 "content": candidate.content,
#                 "category": candidate.category.value,
#                 "priority": candidate.priority,
#                 "is_active": True,
#                 "source": "explicit",
#                 "created_at": datetime.now().isoformat(),
#             })
#         else:
#             # 保留为候选，等待重复证据
#             logger.debug(f"【A2候选】保留待证: {candidate.content[:30]}...")
#             existing_list.append({
#                 "content": candidate.content,
#                 "category": candidate.category.value,
#                 "priority": candidate.priority,
#                 "is_active": False,
#                 "evidence_count": candidate.evidence_count,
#                 "source": "inference",
#                 "needs_repeated_evidence": True,
#                 "created_at": datetime.now().isoformat(),
#             })
#
#     redis_client.set(a2_key, json.dumps(existing_list, ensure_ascii=False))
#
#
# async def _process_current_state_candidates(
#     candidates: list,
#     user_id: str,
#     llm_id: str,
#     current_round: int,
# ) -> None:
#     """处理当前状态候选"""
#     from app.service.chat.state_manager import update_current_state, update_unfinished_items
#     from app.schemas.current_state import UpdateSource, UnfinishedItem, ItemStatus
#
#     for candidate in candidates:
#         if candidate.field_name == "unfinished_items":
#             # 处理未完成事项
#             if candidate.unfinished_content:
#                 item = UnfinishedItem(
#                     content=candidate.unfinished_content,
#                     status=ItemStatus.PENDING,
#                     confidence=candidate.confidence,
#                     expire_rounds=candidate.expire_rounds,
#                     update_reason=candidate.update_reason,
#                 )
#                 update_unfinished_items(user_id, llm_id, [item], current_round)
#         else:
#             # 处理其他状态字段
#             update_current_state(
#                 user_id=user_id,
#                 llm_id=llm_id,
#                 field_name=candidate.field_name,
#                 new_value=candidate.value,
#                 confidence=candidate.confidence,
#                 source=UpdateSource.SUMMARY,
#                 expire_rounds=candidate.expire_rounds,
#                 reason=candidate.update_reason,
#                 current_round=current_round,
#             )
#         logger.debug(f"【状态候选写入】{candidate.field_name}: {candidate.value}")
#
#
# async def _process_time_node_candidates(
#     candidates: list,
#     user_id: str,
#     llm_id: str,
# ) -> None:
#     """处理时间节点候选（实时注入方案：直接写入 unfinished_items）"""
#     from app.service.chat.time_node_service import write_unfinished_item_from_time_expression
#
#     for candidate in candidates:
#         if not candidate.is_valid_time:
#             continue
#
#         # 直接写入 unfinished_items
#         written = write_unfinished_item_from_time_expression(
#             user_id=user_id,
#             llm_id=llm_id,
#             content=candidate.content,
#             time_expression=candidate.time_expression,
#             source_round=candidate.source_round,
#         )
#
#         if written:
#             logger.info(f"【实时注入】时间表达写入: {candidate.time_expression}: {candidate.content[:30]}...")


async def _process_history_event_candidates(
    events: list,
    user_id: str,
    llm_id: str,
) -> None:
    """
    处理历史事件候选

    阶段3：先做简单去重，再写入 memory_bank
    """
    if not events:
        return

    # 转换为兼容格式（阶段4：保留检索就绪字段）
    compatible_events = []
    for event in events:
        compatible_events.append({
            "event_id": event.event_id,
            "time": event.occurred_at,
            "occurred_at": event.occurred_at,
            "last_seen_at": event.last_seen_at,
            "type": event.type.value,
            "actor": event.actor.value,
            "event_type": event.event_type.value,
            "content": event.content,
            "keywords": event.keywords,
            "importance": event.importance,
            "source_snippet": event.source_snippet,
            "source_round": event.source_round,
            "activity_score": event.activity_score,
        })

    # 去重后追加
    await _deduplicate_and_append_events(compatible_events, user_id, llm_id)


async def _deduplicate_and_append_events(
    new_events: List[dict],
    user_id: str,
    llm_id: str,
) -> None:
    """
    去重并追加事件

    阶段3：按 actor + event_type 分桶，短窗去重，长窗续写判断

    去重规则：
    - 短窗（2小时内）：content 相似度 >= 0.85 视为重复
    - 长窗（7天内）：含进展信号视为续写合并
    """
    memory_bank_key = build_memory_key(LLMChatConstant.MEMORY_BANK, user_id, llm_id)

    existing = redis_client.get(memory_bank_key)
    if existing:
        try:
            memory_bank = json.loads(existing)
        except json.JSONDecodeError:
            memory_bank = []
    else:
        memory_bank = []

    # 进展信号词（用于续写判断）
    progress_keywords = ["后来", "结果", "还是", "已经", "最后", "终于", "完成了", "解决了"]

    # 按桶处理
    deduplicated = []
    merged_event_ids = []  # 记录被续写合并的事件ID列表
    for new_event in new_events:
        new_actor = new_event.get("actor", "UNKNOWN")
        new_event_type = new_event.get("event_type", "other")
        new_content = new_event.get("content", "")
        new_time = new_event.get("time", "")

        is_duplicate = False
        is_continuation = False
        merge_target = None

        # 查找同桶事件
        for existing_event in memory_bank[-20:]:  # 检查最近20条
            existing_actor = existing_event.get("actor", "UNKNOWN")
            existing_event_type = existing_event.get("event_type", "other")
            existing_content = existing_event.get("content", "")
            existing_time = existing_event.get("time", "")

            # 同桶判断
            if existing_actor != new_actor or existing_event_type != new_event_type:
                continue

            # 短窗重复判断
            common_words = len(set(new_content.split()) & set(existing_content.split()))
            if common_words >= 3:  # 至少3个词相同
                is_duplicate = True
                logger.debug(f"【事件去重】跳过（短窗重复）: {new_content[:30]}...")
                break

            # 长窗续写判断
            has_progress = any(kw in new_content for kw in progress_keywords)
            if has_progress and existing_content:
                # 判断是否是对同一事件的续写
                overlap = len(set(new_content.split()) & set(existing_content.split()))
                if overlap >= 1:  # 有一定关联
                    is_continuation = True
                    merge_target = existing_event
                    logger.debug(f"【事件续写】合并: {new_content[:30]}...")
                    break

        if is_duplicate:
            continue  # 跳过重复

        if is_continuation and merge_target:
            # 续写合并：更新现有事件（阶段4：保留检索就绪字段）
            merge_target["content"] = f"{merge_target.get('content', '')} [续写] {new_content}"
            merge_target["last_seen_at"] = new_time
            merge_target["importance"] = max(
                merge_target.get("importance", 0.5),
                new_event.get("importance", 0.5)
            )
            merge_target["activity_score"] = max(
                merge_target.get("activity_score", 1.0),
                new_event.get("activity_score", 1.0)
            )
            # 保留关键字段用于后续检索
            if "keywords" in new_event:
                existing_keywords = merge_target.get("keywords", [])
                merged_keywords = list(set(existing_keywords + new_event["keywords"]))
                merge_target["keywords"] = merged_keywords[:5]  # 最多保留5个关键词

            # 记录被合并的事件ID，后续同步到Chroma
            if merge_target.get("event_id"):
                merged_event_ids.append(merge_target.get("event_id"))
        else:
            deduplicated.append(new_event)

    if deduplicated:
        memory_bank.extend(deduplicated)
        redis_client.set(memory_bank_key, json.dumps(memory_bank, ensure_ascii=False))
        logger.info(f"【历史事件入库】新增 {len(deduplicated)} 条（去重后）")

        # 同步新增事件到 Chroma（wire-history-events-to-chroma）
        for event in deduplicated:
            try:
                await chroma_util.upload_history_event(
                    event_content=event.get("content", ""),
                    event_id=event.get("event_id", ""),
                    user_id=user_id,
                    llm_id=llm_id,
                    actor=event.get("actor", "UNKNOWN"),
                    event_type=event.get("event_type", "other"),
                    importance=event.get("importance", 0.5),
                    keywords=event.get("keywords", []),
                    source_round=event.get("source_round", 0),
                    occurred_at=event.get("occurred_at", ""),
                    last_seen_at=event.get("last_seen_at", ""),
                    type=event.get("type", "event"),
                    source_snippet=event.get("source_snippet", ""),
                    activity_score=event.get("activity_score", 1.0),
                )
                logger.debug(f"【Chroma事件同步】成功: {event.get('event_id', '')[:30]}...")
            except Exception as e:
                logger.warning(f"【Chroma事件同步】失败: {event.get('event_id', '')[:30]}..., error={e}")
                # Chroma写入失败不影响memory_bank已成功的持久化

    # 续写合并事件同步到 Chroma（删除旧版本+重写新版本）
    # 从memory_bank中找到所有被续写的事件并同步
    if merged_event_ids:
        for merged_id in merged_event_ids:
            # 从memory_bank中找到该事件
            for updated_event in memory_bank[-20:]:
                if updated_event.get("event_id") == merged_id:
                    try:
                        # 删除旧版本
                        await chroma_util.delete(
                            ChromaTypeConstant.CHAT,
                            event_id=merged_id,
                        )
                        # 上传更新后的版本
                        await chroma_util.upload_history_event(
                            event_content=updated_event.get("content", ""),
                            event_id=merged_id,
                            user_id=user_id,
                            llm_id=llm_id,
                            actor=updated_event.get("actor", "UNKNOWN"),
                            event_type=updated_event.get("event_type", "other"),
                            importance=updated_event.get("importance", 0.5),
                            keywords=updated_event.get("keywords", []),
                            source_round=updated_event.get("source_round", 0),
                            occurred_at=updated_event.get("occurred_at", ""),
                            last_seen_at=updated_event.get("last_seen_at", ""),
                            type=updated_event.get("type", "event"),
                            source_snippet=updated_event.get("source_snippet", ""),
                            activity_score=updated_event.get("activity_score", 1.0),
                        )
                        logger.info(f"【Chroma事件续写同步】成功: {merged_id[:30]}...")
                    except Exception as e:
                        logger.warning(f"【Chroma事件续写同步】失败: {merged_id[:30]}..., error={e}")
                        # Chroma续写同步失败不影响memory_bank已成功的持久化
                    break


async def async_summary_msg(recent_msg_key: str, recent_msg_size: int, user_id: str, llm_id: str) -> None:
    """
    异步消息总结主流程（每7轮触发一次）

    流程：
    1. 取出最近消息，保留最近 RECENT_MSG_KEEP_SIZE 条
    2. 总结消息存入向量数据库
    3. 提取四路候选并分流
    4. 各路候选分别写回
    5. 更新用户画像

    阶段3升级：
    - 使用候选路由服务进行分流
    - 保留兼容的事件提取和 memory_bank 写入
    """
    if recent_msg_size < SUMMARY_TRIGGER_THRESHOLD:
        return

    pip = redis_client.pipeline()
    # 获取最老的几条消息
    pip.lrange(recent_msg_key, RECENT_MSG_KEEP_SIZE, -1)
    # 保留最近的消息
    pip.ltrim(recent_msg_key, 0, RECENT_MSG_KEEP_SIZE - 1)

    result = pip.execute()
    recent_msg_list: list[str] = result[0]
    recent_msg_list.reverse()

    logger.debug(f"记忆总结触发: 原始 {recent_msg_size} 条, 保留 {RECENT_MSG_KEEP_SIZE} 条, 总结 {len(recent_msg_list)} 条")

    # 1. 上传向量数据库（获取总结文本）
    summary_text = await _summary_and_upload(recent_msg_list, user_id, llm_id)

    # 2. 简化链路：直接调用 A2 边界提取、user_profile 更新与 memory_bank 保底沉淀
    # 移除通用 candidate 分流总线，改为职责收敛的三路处理
    from app.service.chat.a2_boundary_service import update_a2_boundaries_in_summary

    # 2.1 A2 边界提取与写回
    await update_a2_boundaries_in_summary(user_id, llm_id, summary_text)

    # 2.2 user_profile 更新（后续任务 3.3 会调整）
    # TODO: 调整 user_profile 更新链路（任务 3.3）

    # 3. 兼容链路：保留旧事件提取作为 memory_bank 保底
    events = await _extract_memory_events(recent_msg_list)
    if events:
        await _append_to_memory_bank(events, user_id, llm_id)

    # 4. 压缩 memory_bank
    await _compress_memory_bank_if_needed(user_id, llm_id)

    # 5. 更新用户画像（保持 summary 周期触发）
    await update_user_profile_in_summary(user_id, llm_id, recent_msg_list)