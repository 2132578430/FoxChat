"""
A2 候选提取服务模块

职责：
- 从 summary batch 中提取 A2 候选（长期边界、高优先级禁忌、稳定偏好）
- 判断候选类型（explicit_boundary vs stable_preference）
- 为候选设置路由元数据

阶段3第一版规则：
- 高优先级边界类信息直接提升（用户明确表达）
- 长期偏好需要重复证据后再提升

A2 边界类别：
- 称呼禁忌：不要叫用户某个称呼
- 话题禁忌：不要聊某个话题
- 互动方式禁忌：不要使用某种互动方式
- 隐私边界：明确的隐私限制
- 身体或亲密边界：身体/亲密相关的边界
"""

import re
from typing import List, Tuple

from loguru import logger

from app.schemas.summary_candidate import (
    A2Candidate,
    A2BoundaryCategory,
    ChangeType,
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

# 长期偏好关键词模式（需要重复证据）
STABLE_PREFERENCE_PATTERNS = [
    r"我更喜欢",
    r"我比较喜欢",
    r"我一直都喜欢",
    r"我一直都不喜欢",
    r"我习惯",
    r"我的习惯是",
    r"我倾向于",
    r"我一直倾向",
]

# 高风险踩雷判定词
HIGH_RISK_KEYWORDS = [
    "创伤", "触发", "敏感", "过敏", "害怕", "恐惧",
    "痛苦", "难受", "难过", "受伤", "伤害",
]


def extract_a2_candidates_from_summary(
    summary_text: str,
    current_round: int,
) -> List[A2Candidate]:
    """
    从 summary batch 文本中提取 A2 候选

    Args:
        summary_text: summary batch 文本
        current_round: 当前全局轮数

    Returns:
        A2Candidate 列表
    """
    candidates = []

    # 1. 提取明确边界（立即提升）
    explicit_candidates = _extract_explicit_boundaries(summary_text, current_round)
    candidates.extend(explicit_candidates)

    # 2. 提取长期偏好候选（需要重复证据）
    preference_candidates = _extract_stable_preferences(summary_text, current_round)
    candidates.extend(preference_candidates)

    # 3. 检测高风险踩雷信号
    high_risk_candidates = _detect_high_risk_signals(summary_text, current_round)
    candidates.extend(high_risk_candidates)

    logger.debug(f"【A2候选提取】共提取 {len(candidates)} 条候选")
    return candidates


def _extract_explicit_boundaries(
    text: str,
    current_round: int,
) -> List[A2Candidate]:
    """
    提取用户明确表达的边界候选

    这些候选应该立即提升为 A2。
    """
    candidates = []

    for category, patterns in EXPLICIT_BOUNDARY_PATTERNS.items():
        for pattern in patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                # 提取上下文
                start = max(0, match.start() - 20)
                end = min(len(text), match.end() + 30)
                context = text[start:end].strip()

                candidate = A2Candidate(
                    content=_clean_boundary_content(match.group(), context),
                    category=category,
                    is_explicit=True,
                    confidence=0.95,
                    priority="critical" if category in [
                        A2BoundaryCategory.PRIVACY_BOUNDARY,
                        A2BoundaryCategory.INTIMACY_BOUNDARY,
                    ] else "high",
                    evidence_count=1,
                    first_seen_round=current_round,
                    last_seen_round=current_round,
                    source_snippet=context,
                    change_type=ChangeType.NEW_ENTRY,
                    target_layer="a2",
                    why_routed=f"命中明确边界模式: {pattern}",
                    needs_repeated_evidence=False,
                )
                candidates.append(candidate)
                logger.debug(f"【明确边界】{category}: {candidate.content[:30]}...")

    return candidates


def _extract_stable_preferences(
    text: str,
    current_round: int,
) -> List[A2Candidate]:
    """
    提取长期偏好候选

    这些候选需要重复证据后再提升。
    """
    candidates = []

    for pattern in STABLE_PREFERENCE_PATTERNS:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            # 提取上下文
            start = max(0, match.start() - 10)
            end = min(len(text), match.end() + 40)
            context = text[start:end].strip()

            candidate = A2Candidate(
                content=_clean_preference_content(match.group(), context),
                category=A2BoundaryCategory.OTHER,
                is_explicit=False,
                confidence=0.7,
                priority="high",
                evidence_count=1,
                first_seen_round=current_round,
                last_seen_round=current_round,
                source_snippet=context,
                change_type=ChangeType.NEW_ENTRY,
                target_layer="a2",
                why_routed=f"命中长期偏好模式: {pattern}，需重复证据",
                needs_repeated_evidence=True,
            )
            candidates.append(candidate)
            logger.debug(f"【长期偏好候选】{candidate.content[:30]}... (需重复证据)")

    return candidates


def _detect_high_risk_signals(
    text: str,
    current_round: int,
) -> List[A2Candidate]:
    """
    检测高风险踩雷信号

    如果文本中包含高风险关键词，且同时表达了某种限制或拒绝，
    则应该作为高优先级边界处理。
    """
    candidates = []

    # 检查高风险关键词
    has_high_risk = any(kw in text.lower() for kw in HIGH_RISK_KEYWORDS)

    if not has_high_risk:
        return candidates

    # 如果有高风险关键词，检查是否有拒绝/限制表达
    refusal_patterns = [
        r"不要",
        r"别",
        r"不想",
        r"不能",
        r"不行",
        r"拒绝",
    ]

    for pattern in refusal_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            # 找到拒绝表达 + 高风险关键词，构建边界候选
            candidate = A2Candidate(
                content=_extract_high_risk_content(text),
                category=A2BoundaryCategory.OTHER,
                is_explicit=True,
                confidence=0.9,
                priority="critical",
                evidence_count=1,
                first_seen_round=current_round,
                last_seen_round=current_round,
                source_snippet=text[:100],
                change_type=ChangeType.NEW_ENTRY,
                target_layer="a2",
                why_routed="高风险踩雷信号 + 拒绝表达",
                needs_repeated_evidence=False,
            )
            candidates.append(candidate)
            logger.warning(f"【高风险边界】{candidate.content[:30]}... (立即提升)")
            break

    return candidates


def _clean_boundary_content(match_text: str, context: str) -> str:
    """清理边界内容，生成简洁描述"""
    # 提取核心内容
    content = match_text.strip()

    # 如果上下文更完整，使用上下文片段
    if len(context) > len(match_text) and len(context) < 60:
        content = context

    # 去除多余空白
    content = re.sub(r"\s+", " ", content)

    return content[:50] if len(content) > 50 else content


def _clean_preference_content(match_text: str, context: str) -> str:
    """清理偏好内容"""
    content = context.strip()
    content = re.sub(r"\s+", " ", content)
    return content[:50] if len(content) > 50 else content


def _extract_high_risk_content(text: str) -> str:
    """提取高风险内容"""
    # 找到包含高风险关键词的句子
    sentences = re.split(r"[。！？\n]", text)
    for sentence in sentences:
        if any(kw in sentence.lower() for kw in HIGH_RISK_KEYWORDS):
            return sentence.strip()[:50]
    return text[:50]


def classify_a2_change_type(
    new_candidate: A2Candidate,
    existing_candidates: List[A2Candidate],
) -> Tuple[ChangeType, str]:
    """
    判断 A2 候选的变化类型

    Args:
        new_candidate: 新候选
        existing_candidates: 已存在的候选列表

    Returns:
        (change_type, reason)
    """
    # 检查是否与已有候选语义等价
    for existing in existing_candidates:
        if _is_semantically_equivalent(new_candidate.content, existing.content):
            # 语义等价，判断是否需要更新证据
            if new_candidate.is_explicit and not existing.is_explicit:
                return ChangeType.CONTENT_UPDATE, "来源等级提升: 从推断变为明确表达"
            if new_candidate.confidence > existing.confidence + 0.1:
                return ChangeType.CONTENT_UPDATE, "置信度提升"
            return ChangeType.REFRESH_ONLY, "语义等价，仅刷新证据计数"

    return ChangeType.NEW_ENTRY, "新候选"


def _is_semantically_equivalent(text1: str, text2: str) -> bool:
    """判断两个文本是否语义等价（简化版）"""
    # 简化版：关键词重叠 + 核心词匹配
    text1_lower = text1.lower()
    text2_lower = text2.lower()

    # 检查核心关键词
    core_keywords = ["不要", "别", "喜欢", "习惯", "倾向"]
    for kw in core_keywords:
        if kw in text1_lower and kw in text2_lower:
            # 有共同核心词，进一步检查内容相似
            words1 = set(text1_lower.split())
            words2 = set(text2_lower.split())
            overlap = len(words1 & words2)
            if overlap >= 2:
                return True

    return False


def should_promote_a2_candidate(
    candidate: A2Candidate,
    promotion_rule: str = "default",
) -> bool:
    """
    判断 A2 候选是否应该提升为常驻 A2

    Args:
        candidate: A2 候选
        promotion_rule: 提升规则（default / strict / relaxed）

    Returns:
        是否应该提升
    """
    # 明确边界直接提升
    if candidate.should_promote_immediately():
        return True

    # 根据规则判断重复证据
    required_count = {
        "default": 2,
        "strict": 3,
        "relaxed": 1,
    }.get(promotion_rule, 2)

    return candidate.has_enough_evidence(required_count)