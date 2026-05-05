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

import asyncio
import json
import re
import time
from datetime import datetime
from typing import List

from loguru import logger

from app.common.constant.ChromaTypeConstant import ChromaTypeConstant
from app.common.constant.FileTypeConstant import FileTypeConstant
from app.common.constant.LLMChatConstant import LLMChatConstant, build_memory_key
from app.core.db.redis_client import redis_client
from app.core.llm_model import model as llm_model
from app.core.prompts.prompt_manager import PromptManager
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

    prompt_str = await PromptManager.get_prompt("memory_summary")
    prompt_str = escape_template(prompt_str, ["recent_msg_list"])
    template = ChatPromptTemplate(
        [
            ("system", prompt_str),
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

    prompt_str = await PromptManager.get_prompt("memory_bank_compress")
    prompt_str = escape_template(prompt_str, ["target_size", "memory_bank_json"])
    compress_prompt = prompt_str.format(
        target_size=MEMORY_BANK_COMPRESS_TARGET,
        memory_bank_json=json.dumps(memory_bank, ensure_ascii=False, indent=2),
    )

    llm = await llm_model.get_memory_model()
    compressed = await llm.ainvoke(compress_prompt)

    try:
        compressed_memory_bank = json.loads(compressed)
        redis_client.set(memory_bank_key, json.dumps(compressed_memory_bank, ensure_ascii=False))
        logger.info(f"memory_bank 压缩完成: {len(memory_bank)} -> {len(compressed_memory_bank)} 条")
    except json.JSONDecodeError:
        logger.warning(f"memory_bank 压缩 JSON 解析失败")


async def _extract_compress_events(recent_msg_list: List[str], user_id: str, llm_id: str) -> None:
    """
    事件处理全链：提取 → 追加 → 压缩（内部串行，外部并行）

    合并三个依赖任务为单次异步调用：
    1. 提取事件 (_extract_memory_events)
    2. 追加到 memory_bank (_append_to_memory_bank)
    3. 压缩 memory_bank (_compress_memory_bank_if_needed)

    错误处理：
    - 每步独立 try-except
    - 失败不中断流程（容错优先）
    - 关键路径记录 ERROR，其他 WARNING

    Args:
        recent_msg_list: 最近消息列表
        user_id: 用户 ID
        llm_id: 角色 ID
    """
    try:
        # 步骤1: 提取事件
        logger.info("[Event Chain Task] 开始提取事件...")
        events = await _extract_memory_events(recent_msg_list)

        if not events:
            logger.info("[Event Chain Task] 未提取到事件，跳过后续步骤")
            return

        # 步骤2: 追加到 memory_bank
        try:
            await _append_to_memory_bank(events, user_id, llm_id)
            logger.info(f"[Event Chain Task] 已追加 {len(events)} 条事件到 memory_bank")
        except Exception as e:
            logger.error(f"[Event Chain Task] 追加 memory_bank 失败: {e}")
            # 追加失败则不执行压缩（数据不完整）
            return

        # 步骤3: 压缩 memory_bank（如需要）
        try:
            await _compress_memory_bank_if_needed(user_id, llm_id)
            logger.info("[Event Chain Task] memory_bank 压缩检查完成")
        except Exception as e:
            logger.warning(f"[Event Chain Task] memory_bank 压缩失败: {e}")
            # 压缩失败不影响已追加数据

    except Exception as e:
        logger.error(f"[Event Chain Task] 事件处理链异常: {e}")


async def _update_a2_boundaries_parallel(recent_msg_list: List[str], user_id: str, llm_id: str) -> None:
    """
    A2 边界并行处理（直接处理原始对话，不依赖 summary_text）

    并发改造：
    - 输入源改为 recent_msg_list（而非 summary_text）
    - 合并为单文本后进行正则提取
    - 与其他总结任务并发执行

    错误处理：
    - A2 边界为非关键任务，失败可容忍
    - 记录 WARNING 级别日志
    - 不中断其他并发任务

    Args:
        recent_msg_list: 最近消息列表
        user_id: 用户 ID
        llm_id: 角色 ID
    """
    try:
        # 合并原始对话为单文本
        combined_text = "\n".join(recent_msg_list)

        if not combined_text.strip():
            logger.debug("[A2 Boundary Task] 原始对话为空，跳过边界提取")
            return

        # 调用 A2 边界服务（传入原始对话）
        from app.service.chat.a2_boundary_service import update_a2_boundaries_from_text

        await update_a2_boundaries_from_text(combined_text, user_id, llm_id)
        logger.info("[A2 Boundary Task] A2 边界提取完成")

    except Exception as e:
        logger.warning(f"[A2 Boundary Task] A2 边界提取失败: {e}")
        # A2 边界为非关键任务，失败不影响其他流程


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


async def async_summary_msg_parallel(recent_msg_key: str, recent_msg_size: int, user_id: str, llm_id: str) -> None:
    """
    异步消息总结主流程（并发版本，每18轮触发一次）

    流程：
    1. 取出最近消息，保留最近 RECENT_MSG_KEEP_SIZE 条
    2. 并发执行四路总结任务：
       - Summary生成和上传
       - A2边界提取
       - 事件提取、追加、压缩（合并链）
       - 用户画像更新

    并发优化：
    - 使用 asyncio.gather() 并发执行独立任务
    - 依赖任务合并为单链，内部串行外部并行
    - 错误处理支持部分成功，各路独立日志

    情绪处理：
    - 情绪分类保持实时（每轮），不批量处理
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

    # 并发执行四路总结任务
    logger.info("【并发总结】开始并发执行四路任务...")

    # 定义并发任务（每个任务独立错误处理）
    async def task_summary():
        """任务1: Summary生成和上传"""
        try:
            result = await _summary_and_upload(recent_msg_list, user_id, llm_id)
            logger.info("[Summary Task] Summary生成和上传完成")
            return result
        except Exception as e:
            logger.error(f"[Summary Task] Summary生成失败: {e}")
            return ""

    async def task_a2_boundaries():
        """任务2: A2边界提取"""
        try:
            await _update_a2_boundaries_parallel(recent_msg_list, user_id, llm_id)
            logger.info("[A2 Boundary Task] A2边界提取完成")
        except Exception as e:
            logger.warning(f"[A2 Boundary Task] A2边界提取失败: {e}")

    async def task_events():
        """任务3: 事件处理链（提取→追加→压缩）"""
        try:
            await _extract_compress_events(recent_msg_list, user_id, llm_id)
            logger.info("[Event Chain Task] 事件处理链完成")
        except Exception as e:
            logger.error(f"[Event Chain Task] 事件处理失败: {e}")

    async def task_user_profile():
        """任务4: 用户画像更新"""
        try:
            await update_user_profile_in_summary(user_id, llm_id, recent_msg_list)
            logger.info("[User Profile Task] 用户画像更新完成")
        except Exception as e:
            logger.warning(f"[User Profile Task] 用户画像更新失败: {e}")

    # 并发执行（使用 asyncio.gather，不阻塞其他任务）
    await asyncio.gather(
        task_summary(),
        task_a2_boundaries(),
        task_events(),
        task_user_profile(),
    )

    logger.info("【并发总结】所有总结任务完成")


# ============================================================================
# Hybrid Trigger: Distributed Lock and Counter-based Task Queuing
# ============================================================================

async def trigger_summary_with_counter(
    recent_msg_key: str,
    recent_msg_size: int,
    user_id: str,
    llm_id: str,
    trigger_source: str
) -> None:
    """
    Trigger summary with distributed lock and counter-based queuing.

    Hybrid trigger mechanism entry point:
    - Timer-based trigger (45s interval, min 18 messages)
    - Max threshold trigger (30 messages forced trigger)

    Lock handling:
    - If lock acquired: execute summary loop to process current and queued tasks
    - If lock failed: increment counter to queue this trigger, return immediately (non-blocking)

    Args:
        recent_msg_key: Redis key for recent messages
        recent_msg_size: Current message count (at trigger time)
        user_id: User ID
        llm_id: LLM/character ID
        trigger_source: "timer" or "max_threshold"
    """
    # Distributed lock key
    lock_key = f"summary_lock:{user_id}:{llm_id}"

    # Attempt to acquire lock (60s timeout to prevent deadlock)
    lock_acquired = redis_client.set(lock_key, "1", nx=True, ex=60)

    if not lock_acquired:
        # Lock held by another trigger → queue this task
        counter_key = f"summary_counter:{user_id}:{llm_id}"
        redis_client.incr(counter_key)
        redis_client.expire(counter_key, 300)  # TTL 5 minutes

        logger.info(f"[{trigger_source}] Lock held for {user_id}:{llm_id}, counter incremented (queued)")
        return  # Return immediately, non-blocking

    try:
        # Lock acquired → execute summary loop
        logger.info(f"[{trigger_source}] Lock acquired for {user_id}:{llm_id}, entering summary loop")

        await execute_summary_loop(
            recent_msg_key,
            user_id,
            llm_id,
            trigger_source
        )

    finally:
        # Always release lock
        redis_client.delete(lock_key)
        logger.debug(f"[{trigger_source}] Lock released for {user_id}:{llm_id}")


async def execute_summary_loop(
    recent_msg_key: str,
    user_id: str,
    llm_id: str,
    trigger_source: str
) -> None:
    """
    Execute summary in loop to process all queued tasks adaptively.

    Loop mechanism:
    - Each iteration processes current recent_msg state (adaptive, not snapshot)
    - After each summary, check counter for queued tasks
    - If counter > 0: decrement and continue loop
    - If counter = 0: delete counter, exit loop

    Adaptive processing:
    - Each iteration uses current recent_msg_size, not trigger-time snapshot
    - User's ongoing messages naturally included in subsequent iterations
    - Always retains last RECENT_MSG_KEEP_SIZE (10) messages

    Args:
        recent_msg_key: Redis key for recent messages
        user_id: User ID
        llm_id: LLM/character ID
        trigger_source: "timer" or "max_threshold" (for logging)
    """
    counter_key = f"summary_counter:{user_id}:{llm_id}"

    # Loop until counter = 0
    while True:
        # Check current recent_msg size (adaptive)
        recent_msg_size = redis_client.llen(recent_msg_key)

        # Check if sufficient messages for summary
        if recent_msg_size < SUMMARY_TRIGGER_THRESHOLD:
            logger.debug(f"[Loop] {user_id}:{llm_id} has {recent_msg_size} messages, below threshold, skip")

            # Check counter for queued tasks
            counter = int(redis_client.get(counter_key) or 0)
            if counter > 0:
                redis_client.decr(counter_key)
                logger.debug(f"[Loop] Counter decremented: {counter} → {counter-1}, continue")
                continue  # Continue to next iteration
            else:
                redis_client.delete(counter_key)
                logger.debug(f"[Loop] Counter = 0, exit loop")
                break  # Exit loop

        # Execute summary (adaptive with current recent_msg_size)
        # Calculate messages to process
        messages_to_process = recent_msg_size - RECENT_MSG_KEEP_SIZE
        logger.info(
            f"[{trigger_source}] {user_id}:{llm_id} 开始总结: "
            f"原始 {recent_msg_size} 条, 处理 {messages_to_process} 条, 保留 {RECENT_MSG_KEEP_SIZE} 条"
        )

        await async_summary_msg_parallel(
            recent_msg_key,
            recent_msg_size,
            user_id,
            llm_id
        )

        # Check remaining messages after summary
        remaining_size = redis_client.llen(recent_msg_key)
        logger.info(
            f"[{trigger_source}] {user_id}:{llm_id} 总结完成: "
            f"处理了 {messages_to_process} 条, 剩余 {remaining_size} 条"
        )

        # Reset timer after summary completion
        from app.service.chat.timer_scheduler import reset_timer
        reset_timer(user_id, llm_id)

        # Check counter for queued tasks
        counter = int(redis_client.get(counter_key) or 0)
        if counter > 0:
            redis_client.decr(counter_key)
            logger.info(f"[Loop] Summary complete, counter decremented: {counter} → {counter-1}, continue")
            continue  # Continue to next iteration for queued task
        else:
            redis_client.delete(counter_key)
            logger.info(f"[Loop] Summary complete, counter = 0, exit loop")
            break  # Exit loop