"""
A2 边界项数据结构定义

职责：
- 定义长期边界、高优先级禁忌和明确长期约束的存储契约
- 简化 candidate 语义，直接作为边界项持久化
"""

from datetime import datetime
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, Field


class A2BoundaryCategory(StrEnum):
    """A2 边界类别"""
    NAMING_PROHIBITION = "称呼禁忌"
    TOPIC_PROHIBITION = "话题禁忌"
    INTERACTION_PROHIBITION = "互动方式禁忌"
    PRIVACY_BOUNDARY = "隐私边界"
    INTIMACY_BOUNDARY = "身体或亲密边界"
    OTHER = "其他"


class A2BoundaryStatus(StrEnum):
    """A2 边界项状态"""
    ACTIVE = "active"      # 当前有效，参与常驻注入
    INACTIVE = "inactive"  # 已被明确撤销、冲突证据确认或人工下线，不再参与注入


class A2BoundaryItem(BaseModel):
    """
    A2 边界项对象

    用于表达长期边界、高优先级禁忌和明确长期约束。
    这是简化 candidate 语义后的直接持久化结构。
    """
    content: str = Field(description="边界内容文本")
    category: A2BoundaryCategory = Field(default=A2BoundaryCategory.OTHER, description="边界类别")
    status: A2BoundaryStatus = Field(default=A2BoundaryStatus.ACTIVE, description="边界状态")
    priority: str = Field(default="high", description="优先级（high/critical）")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="置信度")
    source: str = Field(default="summary", description="来源类型（explicit/summary）")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="创建时间")
    last_updated_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="最后更新时间")
    evidence: Optional[str] = Field(default=None, description="来源摘要或证据片段")

    def is_active_for_injection(self) -> bool:
        """判断是否应该参与 Prompt 注入"""
        return self.status == A2BoundaryStatus.ACTIVE and self.priority in ("high", "critical")

    def should_promote_immediately(self) -> bool:
        """判断是否应该立即提升为 active 边界"""
        # 明确表达的边界类信息直接提升
        if self.source == "explicit" and self.category in [
            A2BoundaryCategory.NAMING_PROHIBITION,
            A2BoundaryCategory.TOPIC_PROHIBITION,
            A2BoundaryCategory.INTERACTION_PROHIBITION,
            A2BoundaryCategory.PRIVACY_BOUNDARY,
            A2BoundaryCategory.INTIMACY_BOUNDARY,
        ]:
            return True
        return False


class A2BoundaryList(BaseModel):
    """
    A2 边界项列表容器

    用于管理多个边界项的存储和更新。
    """
    items: list[A2BoundaryItem] = Field(default_factory=list, description="边界项列表")

    def get_active_items(self) -> list[A2BoundaryItem]:
        """获取所有 active 的边界项"""
        return [item for item in self.items if item.is_active_for_injection()]

    def add_boundary(self, boundary: A2BoundaryItem) -> None:
        """添加新的边界项"""
        self.items.append(boundary)

    def update_boundary(self, index: int, boundary: A2BoundaryItem) -> None:
        """更新指定索引的边界项"""
        if 0 <= index < len(self.items):
            self.items[index] = boundary

    def find_similar_boundary(self, content: str) -> Optional[int]:
        """查找相似边界项的索引"""
        # 简化版：基于内容相似度查找
        for i, item in enumerate(self.items):
            if item.content and content:
                # 简单的文本相似度判断
                common_words = len(set(item.content.split()) & set(content.split()))
                if common_words >= 3:  # 至少3个词相同
                    return i
        return None