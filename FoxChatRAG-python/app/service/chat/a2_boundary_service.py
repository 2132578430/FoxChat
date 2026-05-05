"""
A2 边界服务模块

职责：
- 从 summary 文本中提取长期边界、高优先级禁忌和明确长期约束
- 直接写入 A2 边界项到 Redis，不依赖通用 candidate router
- 简化 candidate 语义，作为边界主来源

simplify-memory-a2-profile:
- 移除 candidate 分流总线依赖
- 直接提取并持久化边界项
- A2 成为边界唯一主来源
"""

import json
import re
from datetime import datetime
from typing import List, Optional

from loguru import logger

from app.common.constant.LLMChatConstant import LLMChatConstant, build_memory_key
from app.core.db.redis_client import redis_client
from app.schemas.a2_boundary import (
    A2BoundaryItem,
    A2BoundaryCategory,
    A2BoundaryStatus,
    A2BoundaryList,
)


# 明确边界关键词模式
EXPLICIT_BOUNDARY_PATTERNS = {
    A2BoundaryCategory.NAMING_PROHIBITION: [
        r"不要叫我",
        r"别叫我",
        r"不要用.*称呼我",
        r"别用.*称呼",
        r"不要喊我",
        r"别喊我",
    ],
    A2BoundaryCategory.TOPIC_PROHIBITION: [
        r"不要聊这个",
        r"别聊这个",
        r"不要再提这个",
        r"别再提这个",
        r"不想聊这个话题",
        r"以后别再提这个",
    ],
    A2BoundaryCategory.INTERACTION_PROHIBITION: [
        r"不要这样回我",
        r"别这样回应",
        r"不要用这种方式",
        r"不要对我.*做",
        r"不要.*对我",
    ],
    A2BoundaryCategory.PRIVACY_BOUNDARY: [
        r"不要问我的隐私",
        r"这是我的隐私",
        r"我不想透露",
        r"不要追问",
    ],
    A2BoundaryCategory.INTIMACY_BOUNDARY: [
        r"不要碰我",
        r"不要.*身体",
        r"保持距离",
        r"不要越界",
    ],
}


def _extract_boundary_content(match_text: str, context: str) -> str:
    """清理和提取边界内容"""
    # 移除多余的标点和空格
    content = re.sub(r'[^\w\s一-鿿]', ' ', match_text)
    content = ' '.join(content.split())

    # 如果内容太短，补充上下文
    if len(content) < 10:
        content = context[:50]

    return content.strip()


def extract_a2_boundaries_from_summary(summary_text: str) -> List[A2BoundaryItem]:
    """
    从 summary 文本中提取 A2 边界项

    Args:
        summary_text: summary 文本

    Returns:
        A2BoundaryItem 列表
    """
    boundaries = []

    # 逐类别提取明确边界
    for category, patterns in EXPLICIT_BOUNDARY_PATTERNS.items():
        for pattern in patterns:
            matches = re.finditer(pattern, summary_text, re.IGNORECASE)
            for match in matches:
                # 提取上下文
                start = max(0, match.start() - 20)
                end = min(len(summary_text), match.end() + 30)
                context = summary_text[start:end].strip()

                content = _extract_boundary_content(match.group(), context)

                # 判断优先级
                priority = "critical" if category in [
                    A2BoundaryCategory.PRIVACY_BOUNDARY,
                    A2BoundaryCategory.INTIMACY_BOUNDARY,
                ] else "high"

                boundary = A2BoundaryItem(
                    content=content,
                    category=category,
                    status=A2BoundaryStatus.ACTIVE,
                    priority=priority,
                    confidence=0.95,
                    source="explicit",
                    created_at=datetime.now().isoformat(),
                    last_updated_at=datetime.now().isoformat(),
                    evidence=context,
                )
                boundaries.append(boundary)
                logger.info(f"【A2边界提取】{category}: {content[:30]}...")

    return boundaries


def _get_a2_boundaries(user_id: str, llm_id: str) -> Optional[A2BoundaryList]:
    """从 Redis 获取 A2 边界项列表"""
    a2_key = build_memory_key(LLMChatConstant.A2_BOUNDARY, user_id, llm_id)
    a2_json = redis_client.get(a2_key)

    if not a2_json:
        logger.debug(f"A2 边界不存在: user_id={user_id}, llm_id={llm_id}")
        return None

    try:
        a2_data = json.loads(a2_json)
        a2_list = A2BoundaryList.model_validate(a2_data)
        logger.debug(f"成功获取 A2 边界: user_id={user_id}, 共 {len(a2_list.items)} 条")
        return a2_list
    except json.JSONDecodeError as e:
        logger.error(f"A2 边界 JSON 解析失败: {e}, user_id={user_id}")
        return None


def _save_a2_boundaries(a2_list: A2BoundaryList, user_id: str, llm_id: str) -> bool:
    """将 A2 边界项列表保存到 Redis"""
    try:
        a2_key = build_memory_key(LLMChatConstant.A2_BOUNDARY, user_id, llm_id)
        redis_client.set(a2_key, json.dumps(a2_list.model_dump(), ensure_ascii=False))
        logger.info(f"A2 边界更新成功: user_id={user_id}, llm_id={llm_id}, 共 {len(a2_list.items)} 条")
        return True
    except Exception as e:
        logger.error(f"A2 边界保存失败: {e}, user_id={user_id}")
        return False


async def update_a2_boundaries_in_summary(
    user_id: str,
    llm_id: str,
    summary_text: str,
) -> None:
    """
    在 summary 流程中更新 A2 边界项

    流程：
    1. 从 summary 文本提取边界项
    2. 获取当前 A2 边界列表
    3. 合并新边界项（避免重复）
    4. 保存到 Redis

    Args:
        user_id: 用户 ID
        llm_id: 角色 ID
        summary_text: summary 文本
    """
    if not summary_text:
        logger.debug(f"summary 文本为空，跳过 A2 边界更新: user_id={user_id}")
        return

    try:
        # 1. 提取新边界项
        new_boundaries = extract_a2_boundaries_from_summary(summary_text)

        if not new_boundaries:
            logger.debug(f"未提取到 A2 边界: user_id={user_id}")
            return

        # 2. 获取当前边界列表
        current_a2_list = _get_a2_boundaries(user_id, llm_id)
        if not current_a2_list:
            current_a2_list = A2BoundaryList(items=[])

        # 3. 合并新边界项
        for new_boundary in new_boundaries:
            # 查找相似边界
            similar_index = current_a2_list.find_similar_boundary(new_boundary.content)

            if similar_index is not None:
                # 更新现有边界（增强置信度）
                existing = current_a2_list.items[similar_index]
                existing.confidence = min(1.0, existing.confidence + 0.05)
                existing.last_updated_at = datetime.now().isoformat()
                existing.evidence = new_boundary.evidence
                logger.debug(f"【A2边界更新】增强: {existing.content[:30]}...")
            else:
                # 添加新边界
                current_a2_list.add_boundary(new_boundary)
                logger.info(f"【A2边界新增】: {new_boundary.content[:30]}...")

        # 4. 保存到 Redis
        success = _save_a2_boundaries(current_a2_list, user_id, llm_id)
        if success:
            logger.info(f"A2 边界更新完成: user_id={user_id}")
        else:
            logger.error(f"A2 边界保存失败: user_id={user_id}")

    except Exception as e:
        logger.error(f"A2 边界更新过程中发生错误: {str(e)[:200]}, user_id={user_id}")


async def update_a2_boundaries_from_text(
    text_content: str,
    user_id: str,
    llm_id: str,
) -> None:
    """
    从任意文本内容提取 A2 边界（并发版本）

    与 update_a2_boundaries_in_summary 的区别：
    - 输入源：任意文本（不限于 summary_text）
    - 执行模式：并发调用，与其他总结任务并行
    - 用途：直接从原始对话提取边界（无需等待 summary）

    流程：
    1. 从文本提取边界项（使用相同的正则逻辑）
    2. 获取当前 A2 边界列表
    3. 合并新边界项（避免重复）
    4. 保存到 Redis

    Args:
        text_content: 任意文本内容（如原始对话、summary等）
        user_id: 用户 ID
        llm_id: 角色 ID
    """
    if not text_content:
        logger.debug(f"[Parallel A2] 文本内容为空，跳过边界提取: user_id={user_id}")
        return

    try:
        # 1. 提取新边界项（使用现有正则逻辑）
        new_boundaries = extract_a2_boundaries_from_summary(text_content)

        if not new_boundaries:
            logger.debug(f"[Parallel A2] 未提取到 A2 边界: user_id={user_id}")
            return

        # 2. 获取当前边界列表
        current_a2_list = _get_a2_boundaries(user_id, llm_id)
        if not current_a2_list:
            current_a2_list = A2BoundaryList(items=[])

        # 3. 合并新边界项（使用现有逻辑）
        for new_boundary in new_boundaries:
            similar_index = current_a2_list.find_similar_boundary(new_boundary.content)

            if similar_index is not None:
                # 更新现有边界（增强置信度）
                existing = current_a2_list.items[similar_index]
                existing.confidence = min(1.0, existing.confidence + 0.05)
                existing.last_updated_at = datetime.now().isoformat()
                existing.evidence = new_boundary.evidence
                logger.debug(f"[Parallel A2] 边界增强: {existing.content[:30]}...")
            else:
                # 添加新边界
                current_a2_list.add_boundary(new_boundary)
                logger.info(f"[Parallel A2] 边界新增: {new_boundary.content[:30]}...")

        # 4. 保存到 Redis
        success = _save_a2_boundaries(current_a2_list, user_id, llm_id)
        if success:
            logger.info(f"[Parallel A2] A2 边界更新完成: user_id={user_id}")
        else:
            logger.warning(f"[Parallel A2] A2 边界保存失败: user_id={user_id}")

    except Exception as e:
        logger.warning(f"[Parallel A2] A2 边界提取过程中发生错误: {str(e)[:200]}, user_id={user_id}")


def get_active_a2_boundaries_for_injection(user_id: str, llm_id: str) -> str:
    """
    获取用于 Prompt 注入的 active A2 边界文本

    Args:
        user_id: 用户 ID
        llm_id: 角色 ID

    Returns:
        格式化后的 A2 边界文本块
    """
    a2_list = _get_a2_boundaries(user_id, llm_id)
    if not a2_list:
        return ""

    active_items = a2_list.get_active_items()
    if not active_items:
        return ""

    # 格式化为文本块
    lines = ["【硬边界】"]
    for item in active_items:
        category = item.category.value
        content = item.content
        lines.append(f"- [{category}] {content}")

    return "\n".join(lines)