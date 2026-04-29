"""
Summary 候选数据结构定义

阶段3新增：用于表达 summary batch 产出的四路候选。
每个 summary batch 会产生：
- A2 候选（长期禁忌、高优先级边界、稳定偏好）
- 当前状态候选（emotion, relation_state, current_focus 等）
- 时间节点候选（未来跟进事项）
- 历史事件候选（过去发生的事实）

路由优先级：
先判 A2 → 再判 B（当前状态） → 再判 T/C（时间节点/历史事件）
"""

from datetime import datetime
from enum import StrEnum
from typing import List, Optional, Dict, Any

from pydantic import BaseModel, Field

from app.schemas.memory_event import MemoryEvent, ChangeType


class CandidateType(StrEnum):
    """候选类型"""
    A2_BOUNDARY = "a2_boundary"
    A2_PREFERENCE = "a2_preference"
    CURRENT_STATE = "current_state"
    TIME_NODE = "time_node"
    HISTORY_EVENT = "history_event"


class A2BoundaryCategory(StrEnum):
    """A2 边界类别"""
    NAMING_PROHIBITION = "称呼禁忌"
    TOPIC_PROHIBITION = "话题禁忌"
    INTERACTION_PROHIBITION = "互动方式禁忌"
    PRIVACY_BOUNDARY = "隐私边界"
    INTIMACY_BOUNDARY = "身体或亲密边界"
    OTHER = "其他"


class A2Candidate(BaseModel):
    """
    A2 候选对象

    用于表达长期边界和稳定偏好候选。
    分为两类：
    - explicit_boundary: 用户明确表达的高优先级边界（立即提升）
    - stable_preference: 需要重复证据的长期偏好（延迟提升）
    """
    content: str = Field(description="边界或偏好内容")
    category: A2BoundaryCategory = Field(default=A2BoundaryCategory.OTHER, description="边界类别")
    is_explicit: bool = Field(default=False, description="是否为用户明确表达")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="置信度")
    priority: str = Field(default="high", description="优先级（high/critical）")
    evidence_count: int = Field(default=1, ge=1, description="证据计数")
    first_seen_round: int = Field(default=0, description="首次出现的轮次")
    last_seen_round: int = Field(default=0, description="最近出现的轮次")
    source_snippet: str = Field(default="", description="来源片段")
    change_type: ChangeType = Field(default=ChangeType.NEW_ENTRY, description="变化类型")
    target_layer: str = Field(default="a2", description="目标层")
    why_routed: str = Field(default="", description="路由原因")
    needs_repeated_evidence: bool = Field(default=True, description="是否需要重复证据才能提升")

    def should_promote_immediately(self) -> bool:
        """判断是否应该立即提升为 A2"""
        # 明确表达的边界类信息直接提升
        if self.is_explicit and self.category in [
            A2BoundaryCategory.NAMING_PROHIBITION,
            A2BoundaryCategory.TOPIC_PROHIBITION,
            A2BoundaryCategory.INTERACTION_PROHIBITION,
            A2BoundaryCategory.PRIVACY_BOUNDARY,
            A2BoundaryCategory.INTIMACY_BOUNDARY,
        ]:
            return True
        return False

    def has_enough_evidence(self, required_count: int = 2) -> bool:
        """判断是否有足够的证据（用于延迟提升）"""
        return self.evidence_count >= required_count


class CurrentStateCandidate(BaseModel):
    """
    当前状态候选对象

    用于表达从 summary 中提取的当前状态候选。
    字段与 CurrentState schema 对应。
    """
    field_name: str = Field(description="字段名（emotion/relation_state/current_focus/interaction_mode）")
    value: str = Field(description="状态值")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="置信度")
    expire_rounds: int = Field(default=-1, description="过期轮数")
    update_reason: str = Field(default="", description="更新原因")
    change_type: ChangeType = Field(default=ChangeType.NEW_ENTRY, description="变化类型")
    target_layer: str = Field(default="current_state", description="目标层")
    why_routed: str = Field(default="", description="路由原因")
    source_round: int = Field(default=0, description="来源轮次")

    # unfinished_items 专用
    unfinished_content: Optional[str] = Field(default=None, description="未完成事项内容（仅 unfinished_items 使用）")
    unfinished_status: Optional[str] = Field(default=None, description="事项状态（仅 unfinished_items 使用）")


class TimeNodeCandidate(BaseModel):
    """
    时间节点候选对象

    用于表达从 summary 中提取的未来跟进候选。
    """
    content: str = Field(description="到期后应重新浮现的事项")
    time_expression: str = Field(default="", description="原始时间表达（如'明天'、'下周'）")
    due_at: str = Field(default="", description="归一化时间锚点")
    precision: str = Field(default="day", description="时间精度（day/datetime）")
    created_from: str = Field(default="user_future_event", description="来源类型")
    change_type: ChangeType = Field(default=ChangeType.NEW_ENTRY, description="变化类型")
    target_layer: str = Field(default="time_node", description="目标层")
    why_routed: str = Field(default="", description="路由原因")
    source_round: int = Field(default=0, description="来源轮次")
    is_valid_time: bool = Field(default=False, description="时间表达是否可归一化")


class SummaryBatchResult(BaseModel):
    """
    Summary Batch 结果容器

    包含四路候选的完整输出。
    """
    a2_candidates: List[A2Candidate] = Field(default_factory=list, description="A2 候选列表")
    current_state_candidates: List[CurrentStateCandidate] = Field(default_factory=list, description="当前状态候选列表")
    time_node_candidates: List[TimeNodeCandidate] = Field(default_factory=list, description="时间节点候选列表")
    history_event_candidates: List[MemoryEvent] = Field(default_factory=list, description="历史事件候选列表")

    batch_round: int = Field(default=0, description="触发总结时的全局轮数")
    batch_time: str = Field(default="", description="触发总结的时间")
    window_size: int = Field(default=0, description="总结窗口大小（消息数）")

    def has_any_candidates(self) -> bool:
        """判断是否有任何候选"""
        return bool(
            self.a2_candidates or
            self.current_state_candidates or
            self.time_node_candidates or
            self.history_event_candidates
        )

    def total_candidates(self) -> int:
        """计算候选总数"""
        return (
            len(self.a2_candidates) +
            len(self.current_state_candidates) +
            len(self.time_node_candidates) +
            len(self.history_event_candidates)
        )

    def get_routing_summary(self) -> Dict[str, int]:
        """获取路由摘要"""
        return {
            "a2": len(self.a2_candidates),
            "current_state": len(self.current_state_candidates),
            "time_node": len(self.time_node_candidates),
            "history_event": len(self.history_event_candidates),
        }


class CandidateRouterResult(BaseModel):
    """
    候选路由结果

    用于记录单个候选的路由决策。
    """
    candidate_type: CandidateType = Field(description="候选类型")
    target_layer: str = Field(description="目标层")
    why_routed: str = Field(default="", description="路由原因")
    source_round: int = Field(default=0, description="来源轮次")
    change_type: ChangeType = Field(default=ChangeType.NEW_ENTRY, description="变化类型")
    is_accepted: bool = Field(default=True, description="是否被接受写入")
    reject_reason: str = Field(default="", description="拒绝原因（如重复、无变化等）")