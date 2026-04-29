"""
时间节点运行时逻辑

职责：
- 创建时间节点（从用户/AI的未来事项表达中提取）
- 归一化时间表达（明天/后天/下周）
- 到期检查与激活
- 激活后路由到 B 层 unfinished_items

第一版范围：
- 只处理 day 精度
- 状态机：pending → active → done
- 不处理复杂时间表达

改造：使用 RedisJSON 原子操作
- 追加节点：JSON.ARRAPPEND（原子）
- 更新节点状态：JSON.SET（原子）
"""

import json
import re
from datetime import datetime, timedelta
from typing import Optional, List

from loguru import logger

from app.common.constant.LLMChatConstant import LLMChatConstant, build_memory_key
from app.core.db.redis_client import redis_client
from app.schemas.time_node import (
    TimeNode,
    TimeNodeList,
    TimeNodeStatus,
    TimePrecision,
    CreatedFrom,
)


# 时间表达匹配模式
TIME_EXPRESSIONS = {
    "明天": timedelta(days=1),
    "后天": timedelta(days=2),
    "下周": timedelta(weeks=1),
}

# 未来事项关键词
FUTURE_EVENT_KEYWORDS = ["考试", "出结果", "面试", "复查", "见面", "约会"]
FUTURE_FOLLOWUP_KEYWORDS = ["提醒", "继续聊", "再聊", "跟进"]


def _get_json_client():
    """获取 RedisJSON 客户端"""
    return redis_client.json()


def _build_nodes_key(user_id: str, llm_id: str) -> str:
    """构建时间节点存储 key"""
    return build_memory_key(LLMChatConstant.ROLE_TIME_NODES, user_id, llm_id)


def _ensure_nodes_key_exists(user_id: str, llm_id: str) -> None:
    """确保时间节点 key 存在，不存在则初始化空数组"""
    key = _build_nodes_key(user_id, llm_id)
    json_client = _get_json_client()

    try:
        json_client.get(key)
    except Exception:
        # 不存在，初始化空数组
        json_client.set(key, '$', {'nodes': []})
        logger.debug(f"【时间节点初始化】已创建空数组: {key}")


def create_time_node(
    user_id: str,
    llm_id: str,
    content: str,
    time_expression: str,
    created_from: CreatedFrom,
    source_round: int = 0,
) -> Optional[TimeNode]:
    """
    创建时间节点（原子追加）

    Args:
        user_id: 用户 ID
        llm_id: 模型 ID
        content: 节点内容（到期后应浮现的事项）
        time_expression: 时间表达（如"明天"）
        created_from: 来源类型
        source_round: 来源轮次

    Returns:
        创建的 TimeNode，若时间表达无法归一化则返回 None
    """
    # 归一化时间
    due_at, precision = _normalize_time_expression(time_expression)

    if not due_at:
        logger.warning(f"【时间节点】无法归一化时间表达: {time_expression}")
        return None

    # 生成唯一 ID
    today_str = datetime.now().strftime("%Y%m%d")
    nodes = get_all_time_nodes(user_id, llm_id)
    sequence = len([n for n in nodes.nodes if n.time_node_id.startswith(f"tn_{today_str}")]) + 1
    time_node_id = f"tn_{today_str}_{sequence:03d}"

    node = TimeNode(
        time_node_id=time_node_id,
        content=content,
        due_at=due_at,
        precision=precision,
        status=TimeNodeStatus.PENDING,
        created_from=created_from,
        source_round=source_round,
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )

    # 原子追加到 Redis
    _append_time_node_atomic(user_id, llm_id, node)

    logger.info(f"【时间节点创建】id={time_node_id}, due_at={due_at}, content={content[:30]}...")
    return node


def _normalize_time_expression(expression: str) -> tuple[Optional[str], TimePrecision]:
    """
    归一化时间表达为 ISO 日期

    Args:
        expression: 时间表达文本（如"明天"、"下周"、"今晚8点"）

    Returns:
        (due_at, precision): 归一化后的日期字符串和精度
    """
    now = datetime.now()

    # 检查预定义的时间表达
    for keyword, delta in TIME_EXPRESSIONS.items():
        if keyword in expression:
            due_date = now + delta
            return due_date.strftime("%Y-%m-%d"), TimePrecision.DAY

    # 检查"今晚X点"格式
    tonight_match = re.search(r"今晚(\d+)点", expression)
    if tonight_match:
        hour = int(tonight_match.group(1))
        due_datetime = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if due_datetime < now:
            due_datetime += timedelta(days=1)
        return due_datetime.isoformat(), TimePrecision.DATETIME

    # 无法归一化
    return None, TimePrecision.DAY


def _append_time_node_atomic(user_id: str, llm_id: str, node: TimeNode) -> None:
    """
    原子追加时间节点到数组（使用 RedisJSON）

    Args:
        user_id: 用户 ID
        llm_id: 模型 ID
        node: TimeNode 对象
    """
    key = _build_nodes_key(user_id, llm_id)
    json_client = _get_json_client()

    # 确保 key 存在
    _ensure_nodes_key_exists(user_id, llm_id)

    # 原子追加节点到数组
    node_dict = node.model_dump()
    json_client.arrappend(key, '$.nodes', node_dict)

    logger.debug(f"【时间节点追加】原子操作: {node.time_node_id}")


def get_all_time_nodes(user_id: str, llm_id: str) -> TimeNodeList:
    """
    获取所有时间节点

    Args:
        user_id: 用户 ID
        llm_id: 模型 ID

    Returns:
        TimeNodeList 对象
    """
    key = _build_nodes_key(user_id, llm_id)
    json_client = _get_json_client()

    try:
        data = json_client.get(key)
        if data and 'nodes' in data:
            return TimeNodeList.model_validate(data)
    except Exception as e:
        logger.debug(f"【时间节点】JSON.GET 失败或 key 不存在: {key}, error: {e}")

    return TimeNodeList()


def get_pending_time_nodes(user_id: str, llm_id: str) -> List[TimeNode]:
    """
    获取所有 pending 状态的时间节点

    Args:
        user_id: 用户 ID
        llm_id: 模型 ID

    Returns:
        pending 状态的 TimeNode 列表
    """
    nodes = get_all_time_nodes(user_id, llm_id)
    return nodes.get_pending_nodes()


def check_due_time_nodes(user_id: str, llm_id: str) -> List[TimeNode]:
    """
    检查已到期的时间节点

    Args:
        user_id: 用户 ID
        llm_id: 模型 ID

    Returns:
        已到期且状态为 pending 的 TimeNode 列表
    """
    pending_nodes = get_pending_time_nodes(user_id, llm_id)
    now = datetime.now()

    due_nodes = []
    for node in pending_nodes:
        if node.is_due(now):
            due_nodes.append(node)
            logger.debug(f"【时间节点到期】id={node.time_node_id}, due_at={node.due_at}")

    return due_nodes


def _find_node_index(user_id: str, llm_id: str, time_node_id: str) -> Optional[int]:
    """
    查找指定节点的数组索引

    Args:
        user_id: 用户 ID
        llm_id: 模型 ID
        time_node_id: 时间节点 ID

    Returns:
        索引位置，若不存在则返回 None
    """
    nodes = get_all_time_nodes(user_id, llm_id)
    for i, node in enumerate(nodes.nodes):
        if node.time_node_id == time_node_id:
            return i
    return None


def activate_time_node(user_id: str, llm_id: str, node: TimeNode) -> None:
    """
    激活时间节点（原子更新状态）

    Args:
        user_id: 用户 ID
        llm_id: 模型 ID
        node: 要激活的 TimeNode
    """
    key = _build_nodes_key(user_id, llm_id)
    json_client = _get_json_client()

    # 查找节点索引
    index = _find_node_index(user_id, llm_id, node.time_node_id)
    if index is None:
        logger.warning(f"【时间节点激活】找不到节点: {node.time_node_id}")
        return

    # 原子更新状态字段
    json_client.set(key, f'$.nodes[{index}].status', TimeNodeStatus.ACTIVE.value)
    json_client.set(key, f'$.nodes[{index}].updated_at', datetime.now().isoformat())

    logger.info(f"【时间节点激活】id={node.time_node_id}, content={node.content[:30]}... (原子操作)")


def mark_time_node_done(user_id: str, llm_id: str, time_node_id: str) -> None:
    """
    标记时间节点为完成（原子更新状态）

    Args:
        user_id: 用户 ID
        llm_id: 模型 ID
        time_node_id: 时间节点 ID
    """
    key = _build_nodes_key(user_id, llm_id)
    json_client = _get_json_client()

    # 查找节点索引
    index = _find_node_index(user_id, llm_id, time_node_id)
    if index is None:
        logger.warning(f"【时间节点完成】找不到节点: {time_node_id}")
        return

    # 原子更新状态字段
    json_client.set(key, f'$.nodes[{index}].status', TimeNodeStatus.DONE.value)
    json_client.set(key, f'$.nodes[{index}].updated_at', datetime.now().isoformat())

    logger.info(f"【时间节点完成】id={time_node_id} (原子操作)")


def extract_time_node_from_text(
    user_id: str,
    llm_id: str,
    text: str,
    is_ai_reply: bool = False,
    source_round: int = 0,
) -> Optional[TimeNode]:
    """
    从文本中提取时间节点

    检测未来事项表达并创建时间节点。

    Args:
        user_id: 用户 ID
        llm_id: 模型 ID
        text: 输入文本
        is_ai_reply: 是否为 AI 回复
        source_round: 来源轮次

    Returns:
        创建的 TimeNode，若无有效表达则返回 None
    """
    # 检测时间表达
    time_expression = None
    for keyword in TIME_EXPRESSIONS.keys():
        if keyword in text:
            time_expression = keyword
            break

    if not time_expression:
        # 检查"今晚X点"
        if re.search(r"今晚\d+点", text):
            time_expression = re.search(r"今晚\d+点", text).group(0)

    if not time_expression:
        return None

    # 确定来源类型
    if is_ai_reply:
        created_from = CreatedFrom.AI_COMMITMENT
    else:
        # 判断是事件还是跟进请求
        if any(kw in text for kw in FUTURE_FOLLOWUP_KEYWORDS):
            created_from = CreatedFrom.USER_FUTURE_FOLLOWUP
        else:
            created_from = CreatedFrom.USER_FUTURE_EVENT

    # 提取内容（简化版：使用整个文本的前50字）
    content = text[:50] if len(text) > 50 else text

    return create_time_node(
        user_id=user_id,
        llm_id=llm_id,
        content=content,
        time_expression=time_expression,
        created_from=created_from,
        source_round=source_round,
    )


def check_and_activate_due_time_nodes(user_id: str, llm_id: str) -> List[str]:
    """
    检查并激活所有到期的时间节点，返回激活的内容列表

    用于在 chat_msg 主流程开始时调用。

    Args:
        user_id: 用户 ID
        llm_id: 模型 ID

    Returns:
        激活的节点内容列表（用于注入 unfinished_items）
    """
    due_nodes = check_due_time_nodes(user_id, llm_id)

    activated_contents = []
    for node in due_nodes:
        activate_time_node(user_id, llm_id, node)
        activated_contents.append(node.content)

    return activated_contents