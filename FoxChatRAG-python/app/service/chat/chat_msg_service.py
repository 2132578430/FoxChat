"""
对话消息服务模块

职责：
- 对话主入口（process_chat_msg）
- 处理用户消息，构建 Prompt
- 调用 LLM 生成回复
- 触发后台任务（记忆总结、状态更新）
- 解析 LLM 返回（去除 think 标签，解析 action 标签）
- 清理对话记忆（clear_chat_memory）

阶段2升级：
- 使用 current_state 替代 emotion_state
- 支持时间节点到期检查
"""

import json
import re
from dataclasses import dataclass
from typing import List, Dict, Optional

from loguru import logger

from app.common.constant.ChromaTypeConstant import ChromaTypeConstant
from app.common.constant.LLMChatConstant import LLMChatConstant, build_memory_key
from app.common.constant.MsgStatusConstant import MsgStatusConstant
from app.core.db.redis_client import redis_client
from app.core.llm_model import model as llm_model
from app.core.prompts.prompt_manager import PromptManager
from app.core.prompts.prompt_template import PromptTemplate
from app.service.chat.state_manager import get_current_state, check_and_expire_fields, increment_round_counter, get_current_round, clean_expired_unfinished_items
from app.service.chat.history_event_retrieval_service import (
    should_trigger_history_retrieval,
    retrieve_history_events_v2,
    format_history_events,
)
from app.exception.BusinessException import BusinessException
from app.schemas import ChatMsgTo, MessageBlock
from app.schemas.current_state import CurrentState, ItemStatus
from app.service.chat.memory_summary_service import async_summary_msg
from app.service.chat.emotion_classifier import classify_and_update_emotion
from app.service.chat.runtime_state_extractor import update_current_state_from_runtime
from app.util import chroma_util, strip_all_tags, strip_think_only
from fastapi import BackgroundTasks
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableLambda
from langchain_core.language_models.chat_models import BaseMessage
from langchain_core.messages import HumanMessage, AIMessage


@dataclass
class ChatMemories:
    """聊天所需的记忆数据容器"""
    init_memory: str
    recent_msg: List[str]
    character_card_json: str
    core_anchor_json: str
    user_profile_json: str
    memory_bank_json: str
    current_state_json: str  # 阶段2：替代 emotion_state
    a2_boundary_json: str = ""  # simplify-memory-a2-profile: A2 边界


@dataclass
class ParsedMemories:
    """解析后的记忆数据容器"""
    character_card_examples: str
    character_card_detail: str
    role_declaration: str
    core_anchor_text: str
    user_profile_summary: str
    memory_bank_summary: str
    current_state: str  # 阶段2：替代 emotion_state


async def process_chat_msg(msg: ChatMsgTo, background_tasks: BackgroundTasks) -> List[MessageBlock]:
    """
    对话主流程

    流程：
    1. 检查并激活到期的时间节点
    2. 批量获取各种记忆
    3. 解析记忆为可读文本
    4. 构建历史消息
    5. 调用 LLM
    6. 保存对话
    7. 触发后台任务（记忆总结、状态更新）
    8. 解析返回
    """
    user_id = msg.userId
    llm_id = msg.llmId
    msg_content = msg.msgContent

    from app.service.chat.state_manager import get_current_round, clean_expired_unfinished_items, update_unfinished_items
    from app.service.chat.time_node_service import route_due_time_nodes
    from app.schemas.current_state import UnfinishedItem, ItemStatus

    # ========== 性能计时起点 ==========
    current_round = get_current_round(user_id, llm_id)
    clean_expired_unfinished_items(user_id, llm_id, current_round)

    # 时间节点到期路由：扫描 pending nodes，到期的路由到 B 层或 C 层
    routing = route_due_time_nodes(user_id, llm_id, current_round)
    if routing["unfinished_items"]:
        activated_items = [
            UnfinishedItem(
                content=item["content"],
                status=ItemStatus.PENDING,
                confidence=item.get("confidence", 0.9),
                expire_rounds=item.get("expire_rounds", 6),
                update_round=current_round,
                update_reason="时间节点到期激活",
                created_at=item.get("created_at"),
                due_at=item.get("due_at"),
            )
            for item in routing["unfinished_items"]
        ]
        update_unfinished_items(user_id, llm_id, activated_items, current_round)
        logger.info(f"【时间节点激活】B层: {len(activated_items)} 条事项写入 unfinished_items")
    retrieval_triggers = routing.get("retrieval_triggers", [])
    if retrieval_triggers:
        logger.info(f"【时间节点路由】C层触发: {len(retrieval_triggers)} 条信号")

    # 1. 获取所有的记忆事件+人物卡+角色核心等等（性能关键点：_fetch_all_memories）
    memories = await _fetch_all_memories(user_id, llm_id)

    # 2. 解析记忆（性能关键点：_parse_all_memories；阶段4：使用 current_round 用于 current_state 过期判断）
    parsed = _parse_all_memories(memories, current_round=current_round)

    # 3. 构建历史消息（性能关键点：_build_history_message）
    history_msg = await _build_history_message(memories.recent_msg)

    # 4. 调用 LLM（性能关键点：_invoke_llm；任务 3.1：传入 current_state、recent_messages、retrieval_triggers）
    recent_msg_key = _build_recent_msg_key(user_id, llm_id)
    chat_response = await _invoke_llm(
        parsed,
        history_msg,
        memories.init_memory,
        msg_content,
        user_id,
        llm_id,
        memories.current_state_json,
        memories.recent_msg,
        retrieval_triggers,
    )
    # ========== 性能计时终点 ==========

    # 5. 保存对话
    msg_count = await _save_chat_to_redis(
        recent_msg_key, msg_content, chat_response
    )

    # 6. 触发后台任务
    background_tasks.add_task(async_summary_msg, recent_msg_key, msg_count, user_id, llm_id)
    pure_text = strip_all_tags(chat_response)

    # 阶段2新增：先递增轮数，获取当前轮数用于过期判断
    current_round = increment_round_counter(user_id, llm_id)

    # 后台任务：情绪分类（传入当前轮数）
    background_tasks.add_task(classify_and_update_emotion, user_id, llm_id, pure_text, current_round)

    # 后台任务：运行时状态更新（传入当前轮数）
    background_tasks.add_task(
        update_current_state_from_runtime,
        user_id, llm_id, msg_content, pure_text, current_round
    )

    # 7. 解析返回
    clean_response = strip_think_only(chat_response)
    return parse_action_tags(clean_response)


async def clear_chat_memory(user_id: str, llm_id: str) -> None:
    """删除对话相关的所有记忆"""
    keys_to_delete = [
        build_memory_key(LLMChatConstant.RAW_EXPERIENCE, user_id, llm_id),
        build_memory_key(LLMChatConstant.CORE_ANCHOR, user_id, llm_id),
        build_memory_key(LLMChatConstant.USER_PROFILE, user_id, llm_id),
        build_memory_key(LLMChatConstant.CHARACTER_CARD, user_id, llm_id),
        build_memory_key(LLMChatConstant.MEMORY_BANK, user_id, llm_id),
        build_memory_key(LLMChatConstant.INIT_MEMORY, user_id, llm_id),
        build_memory_key(LLMChatConstant.RECENT_MSG, user_id, llm_id),
        build_memory_key(LLMChatConstant.ROLE_EMOTION_STATE, user_id, llm_id),
        build_memory_key(LLMChatConstant.ROLE_EMOTION_LOG, user_id, llm_id),
        # 阶段2新增：当前状态和时间节点
        build_memory_key(LLMChatConstant.ROLE_CURRENT_STATE, user_id, llm_id),
        build_memory_key(LLMChatConstant.ROLE_TIME_NODES, user_id, llm_id),
    ]

    # 轮次计数器（不在 memory key 体系内，单独构建）
    round_counter_key = f"chat:memory:{user_id}:{llm_id}:round_counter"

    pip = redis_client.pipeline()
    for key in keys_to_delete:
        pip.delete(key)
    pip.delete(round_counter_key)
    pip.execute()

    await chroma_util.delete(ChromaTypeConstant.CHAT, user_id=user_id, llm_id=llm_id)


async def _fetch_all_memories(user_id: str, llm_id: str) -> ChatMemories:
    """批量获取所有记忆数据"""
    init_memory_key = _build_init_memory_key(user_id, llm_id)
    recent_msg_key = _build_recent_msg_key(user_id, llm_id)
    current_state_key = build_memory_key(LLMChatConstant.ROLE_CURRENT_STATE, user_id, llm_id)
    a2_candidates_key = f"chat:memory:{user_id}:{llm_id}:a2_candidates"

    pip = redis_client.pipeline()
    pip.get(init_memory_key)
    pip.lrange(recent_msg_key, 0, 29)
    pip.get(build_memory_key(LLMChatConstant.CHARACTER_CARD, user_id, llm_id))
    pip.get(build_memory_key(LLMChatConstant.CORE_ANCHOR, user_id, llm_id))
    pip.get(build_memory_key(LLMChatConstant.USER_PROFILE, user_id, llm_id))
    # simplify-memory-a2-profile: 获取 A2 边界
    pip.get(build_memory_key(LLMChatConstant.A2_BOUNDARY, user_id, llm_id))
    pip.get(build_memory_key(LLMChatConstant.MEMORY_BANK, user_id, llm_id))
    # 阶段2：current_state 是 RedisJSON 类型，使用 JSON.GET 命令
    pip.execute_command('JSON.GET', current_state_key)
    # 阶段5：A2 硬边界候选
    pip.get(a2_candidates_key)

    result = pip.execute()

    # JSON.GET 返回的是 dict 或 None，需要转换
    current_state_json = ""
    if result[7]:  # simplify-memory-a2-profile: current_state 在 result[7]（JSON.GET 命令）
        if isinstance(result[7], dict):
            current_state_json = json.dumps(result[7], ensure_ascii=False)
        elif isinstance(result[7], str):
            current_state_json = result[7]
        else:
            # 异常情况：result[7] 不是 dict 也不是 str，可能是旧数据格式错误
            logger.warning(f"current_state Redis 数据格式异常: type={type(result[7])}, value={str(result[7])[:100]}")
            current_state_json = ""

    return ChatMemories(
        init_memory=result[0] or "",
        recent_msg=result[1],
        character_card_json=result[2] or "",
        core_anchor_json=result[3] or "",
        user_profile_json=result[4] or "",
        a2_boundary_json=result[5] or "",  # simplify-memory-a2-profile: A2 边界在 result[5]
        memory_bank_json=result[6] or "",  # simplify-memory-a2-profile: memory_bank 在 result[6]
        current_state_json=current_state_json,  # current_state 在 result[7]
    )


def _parse_all_memories(memories: ChatMemories, current_round: int = 0) -> ParsedMemories:
    """解析所有记忆数据"""
    character_card_examples, character_card_detail = _parse_character_card(memories.character_card_json)
    role_declaration, core_anchor_text = _parse_core_anchor(memories.core_anchor_json)
    # simplify-memory-a2-profile: A2 边界独立解析，不再拼在 user_profile 中
    user_profile_summary = _parse_user_profile(memories.user_profile_json)
    a2_boundary_summary = _parse_a2_boundary(memories.a2_boundary_json)

    # 组合 A2 边界 + user_profile，边界在前
    if a2_boundary_summary:
        user_profile_summary = f"{a2_boundary_summary}\n\n{user_profile_summary}" if user_profile_summary else a2_boundary_summary

    memory_bank_summary = _parse_memory_bank(memories.memory_bank_json)
    # 阶段2：使用 current_state 替代 emotion_state
    # 阶段4：传入 current_round 用于过期判断
    current_state = _parse_current_state(memories.current_state_json, current_round)

    return ParsedMemories(
        character_card_examples=character_card_examples,
        character_card_detail=character_card_detail,
        role_declaration=role_declaration,
        core_anchor_text=core_anchor_text,
        user_profile_summary=user_profile_summary,
        memory_bank_summary=memory_bank_summary,
        current_state=current_state,
    )


def _parse_character_card(json_str: str) -> tuple[str, str]:
    """解析角色卡，返回 (示例对话, 详细描述)"""
    if not json_str:
        return "", ""

    try:
        card = json.loads(json_str)
        examples = card.get("示例对话", "")

        parts = []
        if card.get("性格关键词"):
            parts.append(f"性格关键词：{card['性格关键词']}")
        if card.get("动作风格"):
            parts.append(f"动作风格：{card['动作风格']}")
        if card.get("常用动作"):
            parts.append(f"常用动作：{', '.join(card['常用动作'])}")
        if card.get("核心描述"):
            parts.append(f"核心描述：{card['核心描述']}")

        return examples, "\n".join(parts) if parts else ""
    except json.JSONDecodeError:
        logger.warning("角色卡JSON解析失败")
        return "", ""


def _parse_core_anchor(text: str) -> tuple[str, str]:
    """解析核心锚点，返回 (角色声明, 核心锚点文本)"""
    if not text:
        return "", ""

    role_declaration = ""
    core_anchor_text = ""

    role_match = re.search(r'【角色声明】\s*(.+?)(?=【角色核心锚点】|$)', text, re.DOTALL)
    if role_match:
        role_declaration = role_match.group(1).strip()

    anchor_match = re.search(r'【角色核心锚点】\s*(.+?)(?=【绝对边界】|$)', text, re.DOTALL)
    if anchor_match:
        core_anchor_text = anchor_match.group(1).strip()

    return role_declaration, core_anchor_text


def _build_static_anchors(
    soul: str,
    role_declaration: str,
    core_anchor: str,
    character_card: str,
    character_card_detail: str,
    mes_example: str,
) -> str:
    """
    构建 A1 静态锚点块（阶段5新增）

    按顺序拼接各子部分，非空部分带子标题，空部分跳过。

    Args:
        soul: 角色灵魂内容
        role_declaration: 角色声明内容
        core_anchor: 角色核心锚点内容
        character_card: 角色详细卡内容
        character_card_detail: 角色特征补充内容
        mes_example: 示例对话风格内容

    Returns:
        拼接后的静态锚点字符串
    """
    sections = [
        ("【角色灵魂（Soul）】", soul),
        ("【角色声明】", role_declaration),
        ("【角色核心锚点】", core_anchor),
        ("【角色详细卡】", character_card),
        ("【角色特征补充】", character_card_detail),
        ("【示例对话风格】", mes_example),
    ]

    parts = []
    for header, content in sections:
        if content and content.strip():
            parts.append(f"{header}\n{content.strip()}")

    return "\n\n".join(parts) if parts else ""


def _parse_a2_boundary(a2_boundary_json: str) -> str:
    """
    解析 A2 边界项（simplify-memory-a2-profile）

    从 A2 边界存储中提取 active 边界项并格式化为文本块。
    边界在前，画像在后。
    """
    if not a2_boundary_json:
        return ""

    try:
        from app.schemas.a2_boundary import A2BoundaryList, A2BoundaryStatus

        a2_data = json.loads(a2_boundary_json)
        a2_list = A2BoundaryList.model_validate(a2_data)

        # 只提取 active 且 high/critical priority 的边界项
        active_items = a2_list.get_active_items()

        if not active_items:
            return ""

        # 格式化为边界文本块
        lines = ["【硬边界】"]
        for item in active_items:
            category = item.category.value
            content = item.content
            lines.append(f"- [{category}] {content}")

        return "\n".join(lines)

    except json.JSONDecodeError:
        logger.warning("A2 边界 JSON 解析失败")
        return ""
    except Exception as e:
        logger.warning(f"A2 边界解析失败: {e}")
        return ""


def _parse_a2_boundaries(a2_candidates_json: str) -> str:
    """
    解析 A2 硬边界候选（阶段5新增）

    筛选 is_active=True 且 priority in {high, critical} 的条目，
    按 priority 降序排列，格式化为列表。

    Args:
        a2_candidates_json: a2_candidates Redis key 的 JSON 内容

    Returns:
        格式化后的硬边界字符串，若无有效条目返回空字符串
    """
    if not a2_candidates_json:
        return ""

    try:
        candidates = json.loads(a2_candidates_json)
        if not isinstance(candidates, list):
            return ""

        # 筛选 active 且 high/critical priority
        valid = [
            c for c in candidates
            if c.get("is_active") and c.get("priority") in ("high", "critical")
        ]

        if not valid:
            return ""

        # 按 priority 降序：critical > high
        priority_order = {"critical": 0, "high": 1}
        valid.sort(key=lambda c: priority_order.get(c.get("priority"), 2))

        # 格式化
        lines = []
        for c in valid:
            category = c.get("category", "其他")
            content = c.get("content", "")
            if content:
                lines.append(f"- [{category}] {content}")

        return "\n".join(lines) if lines else ""

    except json.JSONDecodeError:
        return ""


def _parse_user_profile(json_str: str) -> str:
    """解析用户画像（simplify-memory-a2-profile：不再拼接 A2 边界）"""
    if not json_str:
        return ""

    def _is_placeholder(value) -> bool:
        if not value:
            return True
        if isinstance(value, str):
            return value == "[未提及]"
        if isinstance(value, list):
            return all(_is_placeholder(item) for item in value)
        if isinstance(value, dict):
            return all(_is_placeholder(item) for item in value.values())
        return False

    def _format_value(value) -> str:
        if isinstance(value, list):
            return "、".join(str(item) for item in value if not _is_placeholder(item))
        return str(value)

    try:
        profile = json.loads(json_str)
        parts = []
        for dim_key, dim_val in profile.items():
            if isinstance(dim_val, dict):
                items = [
                    f"{k}：{_format_value(v)}"
                    for k, v in dim_val.items()
                    if not _is_placeholder(v)
                ]
                if items:
                    parts.append(f"{dim_key}：{', '.join(items)}")
            elif isinstance(dim_val, list):
                filtered = [_format_value(v) for v in dim_val if not _is_placeholder(v)]
                if filtered:
                    parts.append(f"{dim_key}：{', '.join(filtered)}")
            elif not _is_placeholder(dim_val):
                parts.append(f"{dim_key}：{dim_val}")

        profile_content = "\n".join(parts) if parts else ""

        # simplify-memory-a2-profile: 只返回画像内容，边界由 _parse_a2_boundary 处理
        return profile_content

    except json.JSONDecodeError:
        logger.warning("user_profile JSON解析失败")
        return ""


def _parse_memory_bank(json_str: str) -> str:
    """解析 Memory Bank，只取最近5条作为保底"""
    if not json_str:
        return ""

    try:
        bank = json.loads(json_str)
        if not isinstance(bank, list) or not bank:
            return ""

        recent_items = bank[-5:] if len(bank) > 5 else bank
        logger.info(f"Memory Bank: 共 {len(bank)} 条，注入最近 {len(recent_items)} 条")

        lines = []
        for item in recent_items:
            time_val = item.get("time", "某时")
            content_val = item.get("content", "")
            type_val = item.get("type", "event")
            lines.append(f"- [{time_val}]（{type_val}）{content_val}")
        return "\n".join(lines)
    except json.JSONDecodeError:
        logger.warning("memory_bank JSON解析失败")
        return ""


def _parse_current_state(json_str: str, current_round: int = 0) -> str:
    """
    解析当前状态容器，生成摘要注入 Prompt

    Args:
        json_str: current_state JSON 字符串
        current_round: 当前全局轮数（用于过期判断）

    Returns:
        结构化摘要文本，控制在 80~150 tokens
    """
    if not json_str:
        return _build_default_state_summary()

    try:
        from app.schemas.current_state import CurrentState
        from datetime import datetime

        # 先尝试解析为 JSON 对象
        try:
            state_dict = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(f"current_state JSON 字符串解析失败: {e}")
            return _build_default_state_summary()

        # 验证是否为 dict 类型
        if not isinstance(state_dict, dict):
            logger.warning(f"current_state JSON 不是 dict: type={type(state_dict)}, value={str(state_dict)[:100]}")
            return _build_default_state_summary()

        state = CurrentState.model_validate(state_dict)

        # 获取有效字段
        valid_fields = state.get_valid_fields_for_injection(current_round)

        if not valid_fields:
            return ""  # 无有效字段，返回空字符串

        # 构建摘要（先做 B 层内部去重，再构建 lines）
        lines = []

        # 情绪和关系状态（不受去重影响）
        if "情绪" in valid_fields:
            lines.append(f"- 情绪：{_map_emotion_to_cn(valid_fields['情绪'])}")

        if "关系状态" in valid_fields:
            lines.append(f"- 关系：{valid_fields['关系状态']}")

        # B层内部去重：先判断 current_focus 与 unfinished_items 是否重复
        valid_items = [
            item for item in state.unfinished_items
            if item.is_valid_for_injection(current_round)
        ]

        current_focus_value = valid_fields.get("当前焦点", "")
        should_inject_focus = True  # 默认注入 current_focus

        if valid_items and current_focus_value:
            focus_keywords = set(current_focus_value.split())
            for item in valid_items:
                item_keywords = set(item.content.split())
                overlap = len(focus_keywords & item_keywords) / max(len(focus_keywords), 1) if focus_keywords else 0
                if overlap >= 0.5:  # 50%以上重叠
                    # 优先保留 unfinished_items，跳过 current_focus
                    logger.debug(f"【B层内部去重】current_focus '{current_focus_value}' 与 unfinished_item '{item.content[:30]}...' 重叠，优先保留 unfinished")
                    should_inject_focus = False
                    break

        # 添加 current_focus（如果未被抑制）
        if should_inject_focus and "当前焦点" in valid_fields:
            lines.append(f"- 焦点：{valid_fields['当前焦点']}")

        # 阶段5：未完成事项增加完整时间上下文
        if valid_items:
            today = datetime.now().strftime("%Y-%m-%d")
            for item in valid_items[:2]:  # 最多注入2条
                # 拼接完整时间上下文
                if item.created_at and item.due_at:
                    # 提取日期部分
                    created_date = item.created_at[:10] if len(item.created_at) >= 10 else item.created_at
                    due_date = item.due_at[:10] if len(item.due_at) >= 10 else item.due_at

                    # 完整时间上下文注入
                    lines.append(f"- 未完成：[{created_date}录入] {item.content} [预期{due_date}/今天{today}]")
                else:
                    # 兼容旧数据：没有时间字段时只显示原文
                    lines.append(f"- 未完成：{item.content}")

        if "互动方式" in valid_fields:
            lines.append(f"- 互动方式：{valid_fields['互动方式']}")

        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"current_state JSON 解析失败: {e}")
        return _build_default_state_summary()


def _build_default_state_summary() -> str:
    """构建默认状态摘要（阶段6：空块省略，返回空字符串）"""
    return ""  # 无有效状态时不注入默认值


def _map_emotion_to_cn(emotion: str) -> str:
    """映射情绪标签到中文（兼容旧数据）"""
    emotion_map = {
        "neutral": "平静",
        "happy": "开心",
        "sad": "难过",
        "anger": "生气",
        "angry": "生气",
        "surprise": "惊讶",
        "surprised": "惊讶",
        "fear": "害怕",
        "fearful": "害怕",
        "disgust": "厌恶",
        "disgusted": "厌恶",
        "开心": "开心",
        "悲伤": "悲伤",
        "愤怒": "愤怒",
        "惊讶": "惊讶",
        "恐惧": "恐惧",
        "厌恶": "厌恶",
    }
    return emotion_map.get(emotion.lower(), emotion)


# ==================== 私有函数 - LLM调用 ====================

async def _build_chat_chain():
    """构建对话 Chain"""
    template = ChatPromptTemplate(
        [
            ("system", PromptTemplate.CHAT_SYSTEM_PROMPT_TEMPLATE),
            MessagesPlaceholder("history_msg"),
            ("human", "Reply with a response that matches the current memory and identity according to the above prompts and memory template：\n{user_message}")
        ]
    )

    str_parser = StrOutputParser()
    llm = await llm_model.get_chat_model()
    chain = template | RunnableLambda(_print_template) | llm | str_parser
    return chain


async def _invoke_llm(
    parsed: ParsedMemories,
    history_msg: List[BaseMessage],
    init_memory: str,
    msg_content: str,
    user_id: str,
    llm_id: str,
    current_state_json: str,
    recent_messages: List[str] = None,
    retrieval_triggers: List[Dict] = None
) -> str:
    """调用 LLM 生成回复"""
    from app.service.chat.prompt_payload_builder import build_prompt_payload, payload_to_invoke_dict

    # 构建 Prompt Template（不使用 _build_chat_chain，直接构建以获取 token 信息）
    template = ChatPromptTemplate(
        [
            ("system", PromptTemplate.CHAT_SYSTEM_PROMPT_TEMPLATE),
            MessagesPlaceholder("history_msg"),
            ("human", "Reply with a response that matches the current memory and identity according to the above prompts and memory template:\n{user_message}")
        ]
    )

    # 性能关键点：get_soul
    soul = await PromptManager.get_soul("soul")

    # 阶段4：结构化 history-event 优先，CHAT summary 兜底（任务 3.1）
    # 阶段5修复：传递原始JSON而非解析后的摘要，用于提取current_focus和unfinished_items
    # 性能关键点：_search_relevant_memories
    relevant_memories = await _search_relevant_memories(
        msg_content, user_id, llm_id, current_state_json, recent_messages, retrieval_triggers
    )

    # 阶段5：构建历史上下文（relevant_memories 优先，memory_bank 保底）
    historical_context = relevant_memories if relevant_memories else parsed.memory_bank_summary

    # 阶段5：构建静态锚点块（A1 层）
    static_anchors = _build_static_anchors(
        soul=soul or "",
        role_declaration=parsed.role_declaration,
        core_anchor=parsed.core_anchor_text,
        character_card=init_memory,
        character_card_detail=parsed.character_card_detail,
        mes_example=parsed.character_card_examples,
    )

    # 阶段6：使用统一 payload builder
    payload = build_prompt_payload(
        static_anchors=static_anchors,
        user_profile_summary=parsed.user_profile_summary,
        historical_context=historical_context,
        current_state=parsed.current_state,
        history_msg=history_msg,
        user_message=msg_content,
        recent_messages=recent_messages,
        enable_dedup=True,
        enable_conflict_priority=True,
    )

    # 记录 payload 元信息
    logger.info(f"【Payload】注入: {payload.blocks_injected}, 空块省略: {payload.blocks_omitted}")
    if payload.duplicates_removed:
        logger.info(f"【Payload去重】移除: {payload.duplicates_removed}")

    logger.debug("api调用llm最后提示")

    # 不使用 StrOutputParser，直接获取 AIMessage 以读取 token 信息
    llm = await llm_model.get_chat_model()
    chain_for_tokens = template | RunnableLambda(_print_template) | llm

    # 调用 LLM，获取 AIMessage
    aimessage = await chain_for_tokens.ainvoke(payload_to_invoke_dict(payload))

    # 提取 token 消耗信息（DeepSeek 在 response_metadata 中返回）
    token_info = {}
    if hasattr(aimessage, 'response_metadata'):
        usage = aimessage.response_metadata.get('token_usage', {})
        if usage:
            token_info = {
                'prompt_tokens': usage.get('prompt_tokens', 0),
                'completion_tokens': usage.get('completion_tokens', 0),
                'total_tokens': usage.get('total_tokens', 0),
            }

            # 输出token消耗日志
            logger.info(
                f"【Token消耗】Prompt={token_info['prompt_tokens']}, "
                f"Completion={token_info['completion_tokens']}, "
                f"Total={token_info['total_tokens']}"
            )

    # 备用方案：某些模型通过 usage_metadata 返回
    if not token_info and hasattr(aimessage, 'usage_metadata'):
        meta = aimessage.usage_metadata
        token_info = {
            'prompt_tokens': meta.get('input_tokens', 0),
            'completion_tokens': meta.get('output_tokens', 0),
            'total_tokens': meta.get('input_tokens', 0) + meta.get('output_tokens', 0),
        }
        logger.info(
            f"【Token消耗】Prompt={token_info['prompt_tokens']}, "
            f"Completion={token_info['completion_tokens']}, "
            f"Total={token_info['total_tokens']}"
        )

    # 提取响应文本
    response_text = aimessage.content if hasattr(aimessage, 'content') else str(aimessage)

    return response_text


async def _search_relevant_memories(
    msg_content: str,
    user_id: str,
    llm_id: str,
    current_state_str: str,
    recent_messages: Optional[List[str]] = None,
    retrieval_triggers: Optional[List[Dict]] = None,
) -> str:
    """
    检索相关记忆（阶段4：结构化 history-event 优先，CHAT summary 兜底）

    任务 3.1：切换为结构化事件检索，保持 Prompt 字段契约不变。

    Args:
        msg_content: 用户输入
        user_id: 用户 ID
        llm_id: 模型 ID
        current_state_str: current_state JSON 字符串（用于提取 current_focus、unfinished_items）
        recent_messages: 最近窗口消息列表（用于去重）
        retrieval_triggers: time node 检索触发信号

    Returns:
        格式化后的 relevant_memories 文本块
    """
    try:
        # 解析 current_state 提取触发条件（性能关键点）
        current_focus = None
        unfinished_items_list = None

        if current_state_str:
            try:
                # 兼容性处理：尝试解析 current_state
                state = CurrentState.model_validate_json(current_state_str)
                # 提取当前焦点（如果有效）
                if state.current_focus.value and state.current_focus.confidence >= 0.6:
                    current_focus = state.current_focus.value
                # 提取未完成事项（如果有效）
                unfinished_items_list = [
                    item.content for item in state.unfinished_items
                    if item.status == ItemStatus.PENDING  # 阶段5：使用枚举值比较
                ]
            except Exception as e:
                # 阶段5兼容性增强：解析失败时尝试旧格式或忽略
                logger.debug(f"解析 current_state 失败（将使用空值）: {e}")
                # 尝试解析为旧格式（unfinished_items 可能是字符串列表）
                try:
                    import json
                    raw_state = json.loads(current_state_str)
                    if "unfinished_items" in raw_state:
                        # 兼容旧格式：可能是字符串列表或对象列表
                        items = raw_state["unfinished_items"]
                        unfinished_items_list = []
                        for item in items:
                            if isinstance(item, str):
                                unfinished_items_list.append(item)
                            elif isinstance(item, dict) and "content" in item:
                                unfinished_items_list.append(item["content"])
                except Exception as e2:
                    logger.debug(f"旧格式兼容解析也失败: {e2}")
                    # 最终兜底：使用空列表
                    unfinished_items_list = []

        # 判断是否触发历史检索（性能关键点：should_trigger_history_retrieval；任务 2.2）
        should_trigger = should_trigger_history_retrieval(
            user_input=msg_content,
            current_focus=current_focus,
            unfinished_items=unfinished_items_list,
            retrieval_triggers=retrieval_triggers,
        )

        if should_trigger:
            # 触发检索：使用 V2 混合检索（性能关键点：retrieve_history_events_v2；BM25 + 向量 + Rerank）
            events = await retrieve_history_events_v2(
                query=msg_content,
                user_id=user_id,
                llm_id=llm_id,
                max_results=4,
                recent_messages=recent_messages,
                enable_rerank=True,
            )

            if events:
                # 格式化为稳定短文本块（任务 2.5）
                formatted = format_history_events(events)
                logger.info(f"【历史事件检索】触发成功，返回 {len(events)} 条结构化事件")
                return formatted
            else:
                logger.info("【历史事件检索】触发但无结果，fallback 到 summary 检索")

        # 未触发或无结构化结果：fallback 到 Chroma summary 检索（性能关键点：chroma_util.search；任务 3.3）
        # V2隔离：明确过滤 is_event: False，排除事件污染
        documents = await chroma_util.search(
            ChromaTypeConstant.CHAT,
            msg_content,
            {"user_id": user_id, "llm_id": llm_id, "is_event": False}  # V2: 隔离事件
        )

        if not documents:
            return ""

        lines = []
        for doc in documents[:3]:
            content = doc.page_content.strip()
            if content:
                lines.append(f"- {content}")

        logger.info(f"【Summary 检索】fallback 成功，返回 {len(lines)} 条相关记忆")
        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"检索相关记忆失败: {e}")
        return ""


def _print_template(template_value):
    """打印注入的模板变量（调试用）"""
    logger.debug(f"注入消息：\n{template_value}")
    return template_value


# ==================== 私有函数 - 历史消息 ====================

async def _build_history_message(recent_msg: List[str]) -> List[BaseMessage]:
    """将 Redis 中的消息列表转换为 LangChain 的历史消息格式"""
    if not recent_msg:
        return []

    history_msg: List[BaseMessage] = []
    for msg_json in reversed(recent_msg):
        msg: dict = json.loads(msg_json)
        role = msg.get("role")
        content = msg.get("content")

        if role == "human":
            history_msg.append(HumanMessage(content))
        elif role == "ai":
            history_msg.append(AIMessage(content))
        else:
            raise BusinessException(MsgStatusConstant.UNKNOWN_ROLE_ERROR)

    return history_msg


# ==================== 私有函数 - Redis操作 ====================

def _build_init_memory_key(user_id: str, llm_id: str) -> str:
    """构建初始化记忆的 Redis key"""
    return LLMChatConstant.CHAT_MEMORY + user_id + ":" + llm_id + ":" + LLMChatConstant.INIT_MEMORY


def _build_recent_msg_key(user_id: str, llm_id: str) -> str:
    """构建最近消息的 Redis key"""
    return LLMChatConstant.CHAT_MEMORY + user_id + ":" + llm_id + ":" + LLMChatConstant.RECENT_MSG


async def _save_chat_to_redis(recent_msg_key: str, msg_content: str, chat_response: str) -> int:
    """保存对话到 Redis，返回消息总数"""
    pip = redis_client.pipeline()
    pip.lpush(recent_msg_key, json.dumps({"role": "human", "content": msg_content}))
    pip.lpush(recent_msg_key, json.dumps({"role": "ai", "content": chat_response}))
    pip.llen(recent_msg_key)
    result = pip.execute()
    return result[0]


# ==================== 公开函数 - 响应解析 ====================

def parse_action_tags(content: str) -> List[MessageBlock]:
    """解析 <action> 标签，将消息分割成多个消息块"""
    if not content:
        return []

    pattern = r'<action>(.*?)</action>'
    matches = list(re.finditer(pattern, content, re.DOTALL))

    if not matches:
        return [MessageBlock(type="text", text=content.strip())]

    blocks: List[MessageBlock] = []

    first_action_start = matches[0].start()
    if first_action_start > 0:
        before_text = content[:first_action_start].strip()
        if before_text:
            blocks.append(MessageBlock(type="text", text=before_text))

    for i, match in enumerate(matches):
        action_text = match.group(1).strip()
        match_end = match.end()

        if i < len(matches) - 1:
            next_match_start = matches[i + 1].start()
            after_text = content[match_end:next_match_start].strip()
        else:
            after_text = content[match_end:].strip()

        if after_text:
            blocks.append(MessageBlock(type="action_text", action=action_text, text=after_text))
        else:
            blocks.append(MessageBlock(type="action", action=action_text, text=None))

    return blocks