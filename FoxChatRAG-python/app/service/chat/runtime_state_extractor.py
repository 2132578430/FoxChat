"""
运行时状态候选生成模块

职责：
- 从用户输入中提取 current_focus（当前话题焦点）
- 从 AI 回复中提取 unfinished_items（承诺事项）
- 从对话中检测并创建 time_node（时间节点）
- 组合以上来源，统一更新 current_state

阶段2第一版范围：
- 使用简单规则提取，不依赖 LLM
- 关键词匹配 + 模式检测
"""

import re
from typing import List, Optional

from loguru import logger

from app.service.chat.state_manager import get_current_state, update_current_state, update_unfinished_items
from app.service.chat.time_node_service import extract_time_node_from_text
from app.schemas.current_state import UnfinishedItem, ItemStatus, UpdateSource


# 承诺关键词模式（AI回复中检测）
COMMITMENT_PATTERNS = [
    r"明天(再|继续|帮你)",
    r"下次(再聊|继续)",
    r"之后(再|继续)",
    r"稍后(给你|回复)",
    r"回头(再|帮你)",
]

# 话题焦点关键词（用于从用户输入提取）
FOCUS_KEYWORDS = [
    "考试", "面试", "工作", "学习", "感情", "朋友", "家人",
    "健康", "压力", "焦虑", "失眠", "心情", "失恋",
]


def extract_current_focus(user_input: str, previous_focus: str = "") -> Optional[str]:
    """
    从用户输入中提取当前话题焦点

    Args:
        user_input: 用户输入文本
        previous_focus: 之前的焦点（用于判断延续性）

    Returns:
        提取的焦点短语（4-12字），若无法提取则返回 None
    """
    # 检查是否与之前的焦点相关（延续）
    if previous_focus and previous_focus in user_input:
        logger.debug(f"【焦点延续】{previous_focus}")
        return previous_focus

    # 检测话题关键词
    for keyword in FOCUS_KEYWORDS:
        if keyword in user_input:
            # 尝试提取完整短语
            # 简化版：直接返回关键词
            focus = keyword
            if len(focus) >= 4 and len(focus) <= 12:
                return focus

    # 无法提取
    return None


def extract_unfinished_items_from_ai_reply(ai_reply: str) -> List[UnfinishedItem]:
    """
    从 AI 回复中提取承诺事项

    Args:
        ai_reply: AI 回复文本

    Returns:
        提取的 UnfinishedItem 列表
    """
    items = []

    for pattern in COMMITMENT_PATTERNS:
        if re.search(pattern, ai_reply):
            # 提取承诺内容（简化版：使用匹配的句子）
            match = re.search(pattern, ai_reply)
            if match:
                # 尝试提取完整句子
                sentence_start = max(0, ai_reply.rfind("。", 0, match.start()) + 1)
                sentence_end = ai_reply.find("。", match.end())
                if sentence_end == -1:
                    sentence_end = len(ai_reply)

                content = ai_reply[sentence_start:sentence_end].strip()
                if len(content) > 50:
                    content = content[:50] + "..."

                item = UnfinishedItem(
                    content=content,
                    status=ItemStatus.PENDING,
                    confidence=0.85,
                    expire_rounds=6,
                    update_reason=f"AI承诺: 匹配模式 {pattern}",
                )
                items.append(item)
                logger.debug(f"【承诺提取】{content[:30]}...")
                break  # 一个回复只提取一个承诺

    return items


def update_current_state_from_runtime(
    user_id: str,
    llm_id: str,
    user_input: str,
    ai_reply: str,
    current_round: int = 0,
) -> None:
    """
    从对话中提取并更新 current_state

    组合函数，在每轮对话结束后调用。

    Args:
        user_id: 用户 ID
        llm_id: 模型 ID
        user_input: 用户输入
        ai_reply: AI 回复
        current_round: 当前全局轮数
    """
    # 1. 提取 current_focus
    state = get_current_state(user_id, llm_id, current_round)
    previous_focus = state.current_focus.value

    new_focus = extract_current_focus(user_input, previous_focus)
    if new_focus and new_focus != previous_focus:
        update_current_state(
            user_id=user_id,
            llm_id=llm_id,
            field_name="current_focus",
            new_value=new_focus,
            confidence=0.7,
            source=UpdateSource.RUNTIME,
            expire_rounds=2,
            reason=f"从用户输入提取: {new_focus}",
            current_round=current_round,
        )
        logger.info(f"【焦点更新】current_focus = {new_focus}")

    # 2. 提取 unfinished_items
    new_items = extract_unfinished_items_from_ai_reply(ai_reply)
    if new_items:
        update_unfinished_items(user_id, llm_id, new_items, current_round)
        logger.info(f"【事项添加】{len(new_items)} 条承诺事项")

    # 3. 尝试创建 time_node
    # 从用户输入检测
    user_time_node = extract_time_node_from_text(
        user_id=user_id,
        llm_id=llm_id,
        text=user_input,
        is_ai_reply=False,
        source_round=current_round,
    )

    # 从 AI 回复检测
    ai_time_node = extract_time_node_from_text(
        user_id=user_id,
        llm_id=llm_id,
        text=ai_reply,
        is_ai_reply=True,
        source_round=current_round,
    )

    if user_time_node or ai_time_node:
        logger.info(f"【时间节点】已创建: user={user_time_node is not None}, ai={ai_time_node is not None}")


# 用于导入的简化别名
runtime_update = update_current_state_from_runtime