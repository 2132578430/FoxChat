"""
候选路由服务模块

职责：
- 对 summary batch 产出的候选进行分流
- 按固定优先级路由：A2 → B（当前状态） → T/C（时间节点/历史事件）
- 为每个候选生成路由元数据
- 执行无变化判断

阶段3核心分流逻辑：
- 先判 A2：用户明确边界、高优先级禁忌
- 再判 B：当前情绪、关系态势、话题焦点
- 再判 T/C：未来跟进、历史事实

分流规则基于 `docs/memory_routing.md` 的决策表。
"""

import json
from datetime import datetime, timedelta
from typing import List, Optional
import re

from loguru import logger

from app.schemas.summary_candidate import (
    SummaryBatchResult,
    A2Candidate,
    CurrentStateCandidate,
    TimeNodeCandidate,
    CandidateType,
    CandidateRouterResult,
)
from app.schemas.memory_event import (
    MemoryEvent,
    EventActor,
    EventType,
    EventDetailType,
    ChangeType,
)
from app.service.chat.a2_candidate_service import extract_a2_candidates_from_summary


# 当前状态关键词模式
CURRENT_STATE_PATTERNS = {
    "emotion": {
        "patterns": ["难过", "开心", "愤怒", "焦虑", "恐惧", "平静", "安抚", "放松"],
        "expire_rounds": 3,
    },
    "relation_state": {
        "patterns": ["亲近", "疏离", "紧张", "缓和", "信任", "依赖"],
        "expire_rounds": -1,
    },
    "current_focus": {
        "patterns": ["考试", "工作", "感情", "学业", "健康", "家庭", "朋友"],
        "expire_rounds": 2,
    },
    "interaction_mode": {
        "patterns": ["闲聊", "安慰", "陪伴", "倾听", "解释", "鼓励"],
        "expire_rounds": 3,
    },
}

# 未来时间表达
TIME_EXPRESSIONS = {
    "明天": timedelta(days=1),
    "后天": timedelta(days=2),
    "下周": timedelta(weeks=1),
}

# 未来事项关键词
FUTURE_EVENT_KEYWORDS = ["考试", "出结果", "面试", "复查", "见面", "约会"]
FUTURE_FOLLOWUP_KEYWORDS = ["提醒", "继续聊", "再聊", "跟进", "之后看"]
AI_COMMITMENT_PATTERNS = ["明天再", "下次再聊", "之后再", "稍后给你", "回头帮你"]


def route_summary_candidates(
    summary_text: str,
    current_round: int,
    window_size: int = 0,
) -> SummaryBatchResult:
    """
    对 summary batch 文本进行候选分流

    Args:
        summary_text: summary batch 文本
        current_round: 当前全局轮数
        window_size: 窗口大小（消息数）

    Returns:
        SummaryBatchResult 包含四路候选
    """
    result = SummaryBatchResult(
        batch_round=current_round,
        batch_time=datetime.now().isoformat(),
        window_size=window_size,
    )

    # 1. A2 候选提取（最高优先级）
    a2_candidates = extract_a2_candidates_from_summary(summary_text, current_round)
    result.a2_candidates = a2_candidates

    # 2. 当前状态候选提取（次优先级）
    state_candidates = extract_current_state_candidates(summary_text, current_round)
    result.current_state_candidates = state_candidates

    # 3. 时间节点候选提取
    time_node_candidates = extract_time_node_candidates(summary_text, current_round)
    result.time_node_candidates = time_node_candidates

    # 4. 历史事件候选提取（兜底）
    history_events = extract_history_event_candidates(summary_text, current_round)
    result.history_event_candidates = history_events

    logger.info(f"【候选分流】A2={len(a2_candidates)}, B={len(state_candidates)}, "
                f"T={len(time_node_candidates)}, C={len(history_events)}")

    return result


def extract_current_state_candidates(
    text: str,
    current_round: int,
) -> List[CurrentStateCandidate]:
    """
    提取当前状态候选

    检测情绪、关系、焦点、互动方式等当前有效状态。
    """
    candidates = []

    # 情绪检测
    for emotion_word in CURRENT_STATE_PATTERNS["emotion"]["patterns"]:
        if emotion_word in text:
            candidate = CurrentStateCandidate(
                field_name="emotion",
                value=emotion_word,
                confidence=0.7,
                expire_rounds=CURRENT_STATE_PATTERNS["emotion"]["expire_rounds"],
                update_reason=f"从 summary 检测到情绪词 '{emotion_word}'",
                change_type=ChangeType.NEW_ENTRY,
                target_layer="current_state",
                why_routed=f"命中情绪模式: {emotion_word}",
                source_round=current_round,
            )
            candidates.append(candidate)
            break  # 只取一个情绪

    # 焦点检测
    for focus_word in CURRENT_STATE_PATTERNS["current_focus"]["patterns"]:
        if focus_word in text:
            candidate = CurrentStateCandidate(
                field_name="current_focus",
                value=focus_word,
                confidence=0.7,
                expire_rounds=CURRENT_STATE_PATTERNS["current_focus"]["expire_rounds"],
                update_reason=f"从 summary 检测到焦点词 '{focus_word}'",
                change_type=ChangeType.NEW_ENTRY,
                target_layer="current_state",
                why_routed=f"命中焦点模式: {focus_word}",
                source_round=current_round,
            )
            candidates.append(candidate)
            break  # 只取一个焦点

    # 互动方式检测
    for mode_word in CURRENT_STATE_PATTERNS["interaction_mode"]["patterns"]:
        if mode_word in text:
            candidate = CurrentStateCandidate(
                field_name="interaction_mode",
                value=mode_word,
                confidence=0.6,
                expire_rounds=CURRENT_STATE_PATTERNS["interaction_mode"]["expire_rounds"],
                update_reason=f"从 summary 检测到互动模式 '{mode_word}'",
                change_type=ChangeType.NEW_ENTRY,
                target_layer="current_state",
                why_routed=f"命中互动模式: {mode_word}",
                source_round=current_round,
            )
            candidates.append(candidate)
            break

    # 未完成事项检测（承诺模式）
    for pattern in AI_COMMITMENT_PATTERNS:
        if pattern in text:
            candidate = CurrentStateCandidate(
                field_name="unfinished_items",
                value="pending",
                confidence=0.85,
                expire_rounds=6,
                update_reason=f"检测到 AI 承诺模式 '{pattern}'",
                change_type=ChangeType.NEW_ENTRY,
                target_layer="current_state",
                why_routed=f"命中承诺模式: {pattern}",
                source_round=current_round,
                unfinished_content=_extract_commitment_content(text, pattern),
                unfinished_status="pending",
            )
            candidates.append(candidate)
            break

    return candidates


def extract_time_node_candidates(
    text: str,
    current_round: int,
) -> List[TimeNodeCandidate]:
    """
    提取时间节点候选

    检测未来时间表达和跟进事项。
    """
    candidates = []

    for keyword, delta in TIME_EXPRESSIONS.items():
        if keyword in text:
            # 计算归一化时间
            now = datetime.now()
            due_at = (now + delta).strftime("%Y-%m-%d")

            # 判断来源类型
            created_from = "user_future_event"
            if any(kw in text for kw in FUTURE_FOLLOWUP_KEYWORDS):
                created_from = "user_future_followup"
            elif any(pattern in text for pattern in AI_COMMITMENT_PATTERNS):
                created_from = "ai_commitment"

            # 提取内容
            content = _extract_time_node_content(text, keyword)

            candidate = TimeNodeCandidate(
                content=content,
                time_expression=keyword,
                due_at=due_at,
                precision="day",
                created_from=created_from,
                change_type=ChangeType.NEW_ENTRY,
                target_layer="time_node",
                why_routed=f"检测到未来时间表达 '{keyword}' + {created_from}",
                source_round=current_round,
                is_valid_time=True,
            )
            candidates.append(candidate)
            logger.debug(f"【时间节点候选】{keyword}: {content[:30]}...")

    return candidates


def extract_history_event_candidates(
    text: str,
    current_round: int,
) -> List[MemoryEvent]:
    """
    提取历史事件候选

    作为兜底，提取不属于 A2/B/T 的内容。
    """
    candidates = []

    # 简化版：提取重要句子作为事件候选
    sentences = re.split(r"[。！？\n]", text)
    today_str = datetime.now().strftime("%Y%m%d")

    for i, sentence in enumerate(sentences):
        sentence = sentence.strip()
        if len(sentence) < 10:  # 过短跳过
            continue

        # 判断是否应该作为历史事件
        if _should_be_history_event(sentence):
            event = MemoryEvent(
                event_id=f"evt_{today_str}_{i:03d}",
                occurred_at=datetime.now().isoformat(),
                last_seen_at=datetime.now().isoformat(),
                actor=_detect_actor(sentence),
                type=EventType.EVENT,
                event_type=_detect_event_type(sentence),
                content=sentence[:50],
                keywords=_extract_keywords(sentence),
                importance=_estimate_importance(sentence),
                source_snippet=sentence[:30],
                source_round=current_round,
                activity_score=0.9,
            )
            candidates.append(event)

    # 限制数量
    return candidates[:3]


def _should_be_history_event(sentence: str) -> bool:
    """判断句子是否应该作为历史事件"""
    # 排除边界声明
    boundary_words = ["不要", "别", "以后不"]
    for word in boundary_words:
        if word in sentence and any(kw in sentence for kw in ["叫我", "聊这个", "回应我"]):
            return False

    # 排除未来时间表达
    for keyword in TIME_EXPRESSIONS.keys():
        if keyword in sentence:
            return False

    # 排除纯情绪表达
    emotion_words = ["难过", "开心", "愤怒"]
    if any(word in sentence for word in emotion_words) and len(sentence) < 20:
        return False

    # 包含事实性内容
    fact_indicators = ["去过", "做过", "发生", "经历", "说过", "聊过"]
    return any(indicator in sentence for indicator in fact_indicators)


def _detect_actor(sentence: str) -> EventActor:
    """检测事件主体"""
    if "我" in sentence or "用户" in sentence.lower():
        return EventActor.USER
    if "角色" in sentence or "AI" in sentence or "你" in sentence:
        return EventActor.AI
    return EventActor.UNKNOWN


def _detect_event_type(sentence: str) -> EventDetailType:
    """检测事件细类"""
    if "分享" in sentence or "说过" in sentence:
        return EventDetailType.SHARE_EXPERIENCE
    if "承诺" in sentence or "答应" in sentence:
        return EventDetailType.COMMITMENT
    if "跟进" in sentence or "继续" in sentence:
        return EventDetailType.FOLLOW_UP
    if "关系" in sentence or "信任" in sentence:
        return EventDetailType.RELATION_CHANGE
    return EventDetailType.OTHER


def _extract_keywords(sentence: str) -> List[str]:
    """提取关键词"""
    # 简化版：提取高频词
    keywords = []
    important_words = ["考试", "工作", "感情", "学业", "健康", "家庭", "朋友",
                       "压力", "焦虑", "信任", "承诺", "经历"]

    for word in important_words:
        if word in sentence:
            keywords.append(word)

    return keywords[:3]


def _estimate_importance(sentence: str) -> float:
    """估计重要程度"""
    importance = 0.5

    # 包含重要关键词
    high_importance_words = ["承诺", "边界", "关系", "信任"]
    for word in high_importance_words:
        if word in sentence:
            importance += 0.2

    # 长度适中
    if 20 <= len(sentence) <= 50:
        importance += 0.1

    return min(importance, 1.0)


def _extract_commitment_content(text: str, pattern: str) -> str:
    """提取承诺内容"""
    # 找到包含 pattern 的句子
    sentences = re.split(r"[。！？\n]", text)
    for sentence in sentences:
        if pattern in sentence:
            return sentence.strip()[:40]
    return ""


def _extract_time_node_content(text: str, keyword: str) -> str:
    """提取时间节点内容"""
    # 找到包含关键词的句子
    sentences = re.split(r"[。！？\n]", text)
    for sentence in sentences:
        if keyword in sentence:
            return sentence.strip()[:40]
    return f"跟进事项（{keyword}）"


def classify_candidate_change(
    candidate_type: CandidateType,
    new_candidate: dict,
    existing_items: List[dict],
) -> CandidateRouterResult:
    """
    判断候选的变化类型

    Args:
        candidate_type: 候选类型
        new_candidate: 新候选（字典形式）
        existing_items: 已存在的同类候选

    Returns:
        CandidateRouterResult 包含变化类型和路由结果
    """
    change_type = ChangeType.NEW_ENTRY
    why_routed = "新候选"
    is_accepted = True

    # 简化版去重判断
    content_key = new_candidate.get("content", "")

    for existing in existing_items:
        existing_content = existing.get("content", "")

        # 内容高度相似
        if content_key and existing_content:
            similarity = _text_similarity(content_key, existing_content)
            if similarity > 0.8:
                change_type = ChangeType.REFRESH_ONLY
                why_routed = "内容相似，仅刷新"
                is_accepted = False
                break

    return CandidateRouterResult(
        candidate_type=candidate_type,
        target_layer=candidate_type.value,
        why_routed=why_routed,
        source_round=new_candidate.get("source_round", 0),
        change_type=change_type,
        is_accepted=is_accepted,
        reject_reason="" if is_accepted else "重复或无变化",
    )


def _text_similarity(text1: str, text2: str) -> float:
    """计算文本相似度（简化版）"""
    if not text1 or not text2:
        return 0.0

    # 关键词重叠
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())

    if not words1 or not words2:
        return 0.0

    overlap = len(words1 & words2)
    union = len(words1 | words2)

    return overlap / union if union > 0 else 0.0