"""
当前状态容器 Schema 定义

阶段2核心数据模型，用于表达"当前是什么局面"。

字段说明：
- emotion: 当前情绪（复用现有 emotion_classifier 标签体系）
- relation_state: 关系态势（疏离/中性/亲近/紧张/缓和中）
- current_focus: 当前话题焦点（4-12字短语）
- unfinished_items: 待跟进事项列表
- interaction_mode: 当前互动方式（闲聊/安慰/陪伴等）

过期机制：
- expire_rounds: 相对轮数，表示"多少轮后过期"
- update_round: 上次更新时的全局轮数
- 过期判断: (当前轮数 - 更新轮数) >= 过期轮数
"""

from datetime import datetime
from enum import StrEnum
from typing import List, Optional

from pydantic import BaseModel, Field


class ItemStatus(StrEnum):
    """未完成事项状态"""
    PENDING = "pending"
    DONE = "done"
    CANCELLED = "cancelled"


class UpdateSource(StrEnum):
    """状态更新来源"""
    RUNTIME = "runtime"
    SUMMARY = "summary"
    USER_EXPLICIT = "user_explicit"


class StateField(BaseModel):
    """单个状态字段的通用结构"""
    value: str = Field(default="", description="状态值")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="置信度")
    expire_rounds: int = Field(default=-1, description="相对过期轮数，-1表示永不过期")
    update_round: int = Field(default=0, description="上次更新时的全局轮数")
    update_reason: str = Field(default="", description="更新原因（调试用）")

    def is_expired(self, current_round: int) -> bool:
        """
        判断是否已过期

        Args:
            current_round: 当前全局轮数

        Returns:
            是否过期
        """
        if self.expire_rounds < 0:
            return False  # 永不过期
        return (current_round - self.update_round) >= self.expire_rounds

    def is_valid_for_injection(self, current_round: int) -> bool:
        """判断是否适合注入 Prompt"""
        return self.confidence >= 0.6 and not self.is_expired(current_round)


class UnfinishedItem(BaseModel):
    """未完成事项结构"""
    content: str = Field(description="事项内容")
    status: ItemStatus = Field(default=ItemStatus.PENDING, description="事项状态")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="置信度")
    expire_rounds: int = Field(default=6, description="相对过期轮数")
    update_round: int = Field(default=0, description="创建时的全局轮数")
    update_reason: str = Field(default="", description="更新原因")

    def is_expired(self, current_round: int) -> bool:
        """判断是否已过期"""
        if self.expire_rounds < 0:
            return False
        return (current_round - self.update_round) >= self.expire_rounds

    def is_valid_for_injection(self, current_round: int) -> bool:
        """判断是否适合注入 Prompt"""
        return self.status == ItemStatus.PENDING and not self.is_expired(current_round)


class CurrentState(BaseModel):
    """
    当前工作状态层容器

    对应 docs/layer_definition.md 中的 B 层定义：
    - 每轮常驻注入
    - 各字段独立过期
    - 注入摘要而非原始 JSON
    """
    emotion: StateField = Field(
        default_factory=lambda: StateField(value="平静", confidence=0.5, expire_rounds=3, update_round=0),
        description="当前情绪"
    )
    relation_state: StateField = Field(
        default_factory=lambda: StateField(value="中性", confidence=0.5, expire_rounds=-1, update_round=0),
        description="关系态势"
    )
    current_focus: StateField = Field(
        default_factory=lambda: StateField(value="", confidence=0.0, expire_rounds=2, update_round=0),
        description="当前话题焦点"
    )
    unfinished_items: List[UnfinishedItem] = Field(
        default_factory=list,
        description="待跟进事项"
    )
    interaction_mode: StateField = Field(
        default_factory=lambda: StateField(value="闲聊", confidence=0.5, expire_rounds=3, update_round=0),
        description="互动方式"
    )
    last_update: str = Field(default="", description="最后更新时间（ISO datetime）")
    update_source: UpdateSource = Field(default=UpdateSource.RUNTIME, description="更新来源")

    def get_valid_fields_for_injection(self, current_round: int) -> dict:
        """获取适合注入 Prompt 的有效字段"""
        result = {}

        if self.emotion.is_valid_for_injection(current_round):
            result["情绪"] = self.emotion.value

        if self.relation_state.is_valid_for_injection(current_round):
            result["关系状态"] = self.relation_state.value

        if self.current_focus.is_valid_for_injection(current_round) and self.current_focus.value:
            result["当前焦点"] = self.current_focus.value

        valid_items = [
            item.content for item in self.unfinished_items
            if item.is_valid_for_injection(current_round)
        ]
        if valid_items:
            result["未完成事项"] = valid_items[:2]  # 最多注入2条

        if self.interaction_mode.is_valid_for_injection(current_round):
            result["互动方式"] = self.interaction_mode.value

        return result