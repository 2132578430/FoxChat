"""
历史事件检索服务（阶段4 → 阶段5 V2升级）

职责：
- 从 memory_bank (Redis) 或 Chroma 检索历史事件
- 按 actor、event_type、importance、freshness 等 metadata 过滤与排序
- 结果去重、预算控制
- 格式化为稳定短文本块供 relevant_memories 使用

阶段4第一版：
- 主要数据源：memory_bank (Redis)
- 检索方式：规则过滤 + 重要性排序 + 时间新鲜度排序
- 向量检索：通过 chroma_util.search_history_events (已准备)

阶段5 V2升级：
- 混合检索：BM25关键词召回 + 向量语义检索
- Rerank：FlashrankRerank二次排序
- activity_score纳入排序权重
- 输出契约保持稳定（format_history_events不变）
"""

import json
import re
from datetime import datetime
from typing import List, Optional, Dict, Set

from loguru import logger

from app.common.constant.LLMChatConstant import LLMChatConstant, build_memory_key
from app.core.db.redis_client import redis_client
from app.schemas.memory_event import MemoryEvent, EventActor, EventDetailType
from app.util.chroma_util import search_history_events


# 检索配置
MAX_HISTORY_EVENTS = 4  # 最多返回 4 条
MIN_IMPORTANCE = 0.5  # 最低重要性阈值
MAX_AGE_DAYS = 30  # 最多考虑 30 天内的事件

# 触发规则配置（任务 2.2）
EXPLICIT_RECALL_WORDS = ["上次", "之前", "后来", "你还记得", "前面说过", "那时候", "从前"]


def should_trigger_history_retrieval(
    user_input: str,
    current_focus: Optional[str] = None,
    unfinished_items: Optional[List[str]] = None,
    recent_window_keywords: Optional[Set[str]] = None,
    retrieval_triggers: Optional[List[Dict]] = None,
) -> bool:
    """
    判断是否应该触发历史检索（任务 2.2）

    触发规则：
    1. 显式回忆词
    2. 当前输入命中 current_focus 或 unfinished_items
    3. time node 到期提供的强触发信号
    4. 最近窗口不足以解释语义跳转

    Args:
        user_input: 用户当前输入
        current_focus: 当前焦点（可选）
        unfinished_items: 未完成事项内容列表（可选）
        recent_window_keywords: 最近窗口关键词集合（可选）
        retrieval_triggers: time node 检索触发信号（可选）

    Returns:
        是否触发检索
    """
    # 1. 显式回忆词触发
    for word in EXPLICIT_RECALL_WORDS:
        if word in user_input:
            logger.info(f"【检索触发】显式回忆词: {word}")
            return True

    # 2. current_focus 匹配触发
    if current_focus and current_focus in user_input:
        logger.info(f"【检索触发】当前焦点匹配: {current_focus}")
        return True

    # 3. unfinished_items 匹配触发
    if unfinished_items:
        for item in unfinished_items:
            # 简化版：关键词重叠判断
            item_keywords = set(item.split())
            input_keywords = set(user_input.split())
            overlap = len(item_keywords & input_keywords)
            if overlap >= 2:  # 至少 2 个关键词重叠
                logger.info(f"【检索触发】未完成事项匹配: {item[:30]}...")
                return True

    # 4. time node 强触发信号
    if retrieval_triggers:
        logger.info(f"【检索触发】time node 强触发: {len(retrieval_triggers)} 条信号")
        return True

    # 5. 最近窗口不足以解释语义跳转（简化版：关键词覆盖度判断）
    if recent_window_keywords:
        input_keywords = set(user_input.split())
        # 如果输入中有较多关键词不在最近窗口，可能需要历史背景
        novel_keywords = input_keywords - recent_window_keywords
        if len(novel_keywords) >= 3:  # 至少 3 个新关键词
            logger.debug(f"【检索触发】语义跳转: {len(novel_keywords)} 个新关键词")
            # 第一版保守策略：只在其他触发条件满足时才触发
            # TODO: 后续可增加更精细的语义跳转检测

    return False


def deduplicate_retrieved_events(events: List[MemoryEvent]) -> List[MemoryEvent]:
    """
    去重检索结果，优先保留合并后的续写事件（任务 2.3）

    规则：
    - 同一 event_id 只保留一条
    - 续写事件（带 "[续写]" 标记）优先于旧版本
    - 按重要性排序后去重

    Args:
        events: 原始检索结果

    Returns:
        去重后的结果
    """
    deduplicated = []
    seen_event_ids = set()
    seen_content_hashes = set()

    for event in events:
        # 按 event_id 去重
        if event.event_id and event.event_id in seen_event_ids:
            continue

        # 按内容哈希去重（处理没有 event_id 的旧事件）
        content_hash = hash(event.content[:50])  # 简化版：前 50 字哈希
        if content_hash in seen_content_hashes:
            continue

        # 续写事件特殊处理：替换旧版本
        if "[续写]" in event.content:
            # 尝试找到原始版本并替换
            original_content = event.content.split("[续写]")[0].strip()
            original_hash = hash(original_content[:50])

            # 移除旧版本
            deduplicated = [e for e in deduplicated if hash(e.content[:50]) != original_hash]

        deduplicated.append(event)
        if event.event_id:
            seen_event_ids.add(event.event_id)
        seen_content_hashes.add(content_hash)

    return deduplicated


def deduplicate_with_recent_window(
    events: List[MemoryEvent],
    recent_messages: List[str],
) -> List[MemoryEvent]:
    """
    与最近窗口去重，避免 C 层和 D 层大段重复（任务 2.4）

    Args:
        events: 检索结果
        recent_messages: 最近窗口消息列表

    Returns:
        去重后的事件
    """
    if not recent_messages:
        return events

    # 提取最近窗口的关键词集合
    recent_keywords = set()
    for msg in recent_messages[-6:]:  # 最近 6 条消息
        recent_keywords.update(msg.split())

    deduplicated = []
    for event in events:
        event_keywords = set(event.content.split())

        # 计算与最近窗口的重叠度
        overlap = len(event_keywords & recent_keywords)
        overlap_ratio = overlap / max(len(event_keywords), 1)

        # 如果事件内容主要都在最近窗口中，则抑制
        if overlap_ratio >= 0.7:  # 70% 以上重叠
            logger.debug(f"【最近窗口去重】抑制高重叠事件: {event.content[:30]}...")
            continue

        deduplicated.append(event)

    return deduplicated


def retrieve_history_events_from_memory_bank(
    user_id: str,
    llm_id: str,
    filter_metadata: Optional[Dict] = None,
    max_results: int = MAX_HISTORY_EVENTS,
    recent_messages: Optional[List[str]] = None,
) -> List[MemoryEvent]:
    """
    从 memory_bank (Redis) 检索历史事件

    Args:
        user_id: 用户 ID
        llm_id: 模型 ID
        filter_metadata: 过滤条件，如 {"actor": "USER", "event_type": "commitment"}
        max_results: 返回数量上限
        recent_messages: 最近窗口消息列表（用于去重）

    Returns:
        MemoryEvent 列表，按 importance 和 freshness 排序
    """
    memory_bank_key = build_memory_key(LLMChatConstant.MEMORY_BANK, user_id, llm_id)

    existing = redis_client.get(memory_bank_key)
    if not existing:
        return []

    try:
        memory_bank = json.loads(existing)
    except json.JSONDecodeError:
        logger.warning(f"memory_bank JSON 解析失败: {memory_bank_key}")
        return []

    # 过滤
    candidates = []
    for event_dict in memory_bank:
        # 构建 MemoryEvent 对象（阶段4：兼容新字段）
        try:
            event = MemoryEvent(
                event_id=event_dict.get("event_id", ""),
                occurred_at=event_dict.get("occurred_at", event_dict.get("time", "")),
                last_seen_at=event_dict.get("last_seen_at", event_dict.get("time", "")),
                actor=EventActor(event_dict.get("actor", "UNKNOWN")),
                type=event_dict.get("type", "event"),  # 阶段4：兼容旧字段
                event_type=EventDetailType(event_dict.get("event_type", "other")),
                content=event_dict.get("content", ""),
                keywords=event_dict.get("keywords", []),
                importance=event_dict.get("importance", 0.5),
                source_snippet=event_dict.get("source_snippet", ""),
                source_round=event_dict.get("source_round", 0),
                activity_score=event_dict.get("activity_score", 1.0),
            )
        except Exception as e:
            logger.debug(f"事件转换失败: {e}, 原始: {event_dict}")
            continue

        # 应用 metadata 过滤
        if filter_metadata:
            if "actor" in filter_metadata and event.actor.value != filter_metadata["actor"]:
                continue
            if "event_type" in filter_metadata and event.event_type.value != filter_metadata["event_type"]:
                continue
            if "min_importance" in filter_metadata and event.importance < filter_metadata["min_importance"]:
                continue

        # 过滤低重要性事件
        if event.importance < MIN_IMPORTANCE:
            continue

        candidates.append(event)

    # 排序：importance DESC + freshness DESC
    def sort_key(event: MemoryEvent):
        # 重要性权重：0.5 * importance
        importance_score = event.importance * 0.5

        # 新鲜度权重：根据 last_seen_at 计算
        try:
            last_seen_time = datetime.fromisoformat(event.last_seen_at.replace("Z", "+00:00"))
            days_ago = (datetime.now() - last_seen_time).days
            # 新鲜度得分：越新越高，最多 30 天有效
            freshness_score = max(0, (MAX_AGE_DAYS - days_ago) / MAX_AGE_DAYS) * 0.5
        except Exception:
            freshness_score = 0.0

        return importance_score + freshness_score

    sorted_candidates = sorted(candidates, key=sort_key, reverse=True)

    # 任务 2.3：去重，优先保留续写事件
    deduplicated = deduplicate_retrieved_events(sorted_candidates)

    # 任务 2.4：与最近窗口去重
    if recent_messages:
        deduplicated = deduplicate_with_recent_window(deduplicated, recent_messages)

    # 任务 2.6：预算控制，裁剪低相关/低重要度事件
    if len(deduplicated) > max_results:
        logger.info(f"【历史检索】预算控制: {len(deduplicated)} -> {max_results} 条")
        deduplicated = deduplicated[:max_results]

    return deduplicated


def format_history_events(events: List[MemoryEvent]) -> str:
    """
    格式化历史事件为 stable short text block

    Args:
        events: MemoryEvent 列表

    Returns:
        格式化后的短文本，适合注入 relevant_memories
    """
    if not events:
        return ""

    lines = ["【相关历史事件】"]

    for event in events:
        # 简化表达：只保留核心内容
        actor_prefix = "用户" if event.actor == EventActor.USER else "角色"
        event_line = f"- {actor_prefix}{event.content}"

        # 如果有明确时间锚点，可选择性加上
        if event.occurred_at and len(event.occurred_at) >= 10:
            time_hint = event.occurred_at[:10]  # YYYY-MM-DD
            if time_hint != datetime.now().strftime("%Y-%m-%d"):
                event_line = f"- [{time_hint}] {actor_prefix}{event.content}"

        lines.append(event_line)

    # 总预算控制：最多 300-600 tokens
    return "\n".join(lines)


# ============================================================
# 阶段5 V2升级：混合检索 + Rerank + activity_score排序
# ============================================================

def _bm25_retrieve_from_memory_bank(
    query: str,
    user_id: str,
    llm_id: str,
    max_results: int = 10,
) -> List[MemoryEvent]:
    """
    BM25关键词召回（从memory_bank构建临时索引）

    V2新增：使用jieba分词 + 简化BM25评分

    Args:
        query: 用户查询文本
        user_id: 用户ID
        llm_id: 模型ID
        max_results: 返回数量上限

    Returns:
        MemoryEvent列表（按BM25分数排序）
    """
    import jieba

    memory_bank_key = build_memory_key(LLMChatConstant.MEMORY_BANK, user_id, llm_id)
    existing = redis_client.get(memory_bank_key)
    if not existing:
        return []

    try:
        memory_bank = json.loads(existing)
    except json.JSONDecodeError:
        return []

    # 分词查询
    query_terms = list(jieba.cut(query))
    query_terms = [t for t in query_terms if len(t) > 1]  # 过滤单字

    if not query_terms:
        return []

    candidates = []

    for event_dict in memory_bank:
        content = event_dict.get("content", "")
        if not content:
            continue

        # 分词事件内容
        content_terms = list(jieba.cut(content))
        content_terms_set = set(content_terms)

        # 简化BM25评分：计算查询词在内容中的出现频率和覆盖率
        term_freq = 0
        for term in query_terms:
            if term in content_terms_set:
                term_freq += content.count(term)

        # 覆盖率：有多少查询词命中
        coverage = len([t for t in query_terms if t in content_terms_set]) / len(query_terms)

        # BM25简化分数
        bm25_score = term_freq * 0.5 + coverage * 0.5

        if bm25_score > 0:
            try:
                event = MemoryEvent(
                    event_id=event_dict.get("event_id", ""),
                    occurred_at=event_dict.get("occurred_at", event_dict.get("time", "")),
                    last_seen_at=event_dict.get("last_seen_at", event_dict.get("time", "")),
                    actor=EventActor(event_dict.get("actor", "UNKNOWN")),
                    type=event_dict.get("type", "event"),
                    event_type=EventDetailType(event_dict.get("event_type", "other")),
                    content=content,
                    keywords=event_dict.get("keywords", []),
                    importance=event_dict.get("importance", 0.5),
                    source_snippet=event_dict.get("source_snippet", ""),
                    source_round=event_dict.get("source_round", 0),
                    activity_score=event_dict.get("activity_score", 1.0),
                )
                # 附加BM25分数用于后续合并排序
                event._bm25_score = bm25_score
                candidates.append(event)
            except Exception as e:
                logger.debug(f"BM25事件转换失败: {e}")
                continue

    # 按BM25分数排序
    sorted_candidates = sorted(candidates, key=lambda e: e._bm25_score, reverse=True)

    return sorted_candidates[:max_results]


async def _vector_retrieve_from_chroma(
    query: str,
    user_id: str,
    llm_id: str,
    max_results: int = 10,
) -> List[MemoryEvent]:
    """
    向量语义检索（从Chroma事件库）

    V2新增：使用chroma_util.search_history_events

    Args:
        query: 用户查询文本
        user_id: 用户ID
        llm_id: 模型ID
        max_results: 返回数量上限

    Returns:
        MemoryEvent列表（按向量相似度排序）
    """
    from langchain_core.documents import Document

    documents = await search_history_events(
        query=query,
        user_id=user_id,
        llm_id=llm_id,
        k=max_results,
    )

    events = []
    for doc in documents:
        meta = doc.metadata
        try:
            event = MemoryEvent(
                event_id=meta.get("event_id", ""),
                occurred_at=meta.get("occurred_at", ""),
                last_seen_at=meta.get("last_seen_at", ""),
                actor=EventActor(meta.get("actor", "UNKNOWN")),
                type=meta.get("type", "event"),
                event_type=EventDetailType(meta.get("event_type", "other")),
                content=doc.page_content,
                keywords=meta.get("keywords", []),
                importance=meta.get("importance", 0.5),
                source_snippet=meta.get("source_snippet", ""),
                source_round=meta.get("source_round", 0),
                activity_score=meta.get("activity_score", 1.0),
            )
            # 附加向量分数（如果chroma返回了距离信息）
            event._vector_score = 1.0  # 默认值，实际可从chroma获取
            events.append(event)
        except Exception as e:
            logger.debug(f"向量事件转换失败: {e}")
            continue

    return events


def _is_generic_summary(content: str) -> bool:
    """
    判断内容是否为通用摘要（而非具体事件）

    V2排序规则：通用摘要应被具体事件优先替代

    判断标准：
    - 内容以"用户"、"角色"开头但缺乏具体行为描述
    - 包含大量泛化词汇如"提到了"、"讨论了"、"一般"
    - 无明确时间锚点或具体对象
    """
    generic_markers = ["提到了", "讨论了", "一般", "通常", "经常", "有时候", "偶尔"]
    specific_markers = ["承诺", "约定", "偏好", "边界", "决定", "选择", "要求", "拒绝"]

    content_lower = content.lower()

    # 通用摘要特征
    generic_count = sum(1 for m in generic_markers if m in content)
    # 具体事件特征
    specific_count = sum(1 for m in specific_markers if m in content)

    # 缺乏具体标记且有多条通用标记 -> 可能是摘要
    if generic_count >= 2 and specific_count == 0:
        return True

    # 内容过短且无具体信息
    if len(content) < 20 and specific_count == 0:
        return True

    return False


def _compute_specificity_bonus(event: MemoryEvent) -> float:
    """
    计算事件特异性加分

    V2排序规则：具体、明确的事件应优先于泛化描述

    加分规则：
    - commitment类型：+0.15（承诺/约定是强约束）
    - preference类型：+0.10（偏好是稳定信息）
    - boundary类型：+0.20（边界是最高优先级）
    - 包含明确时间锚点：+0.05
    - 包含明确对象名：+0.05
    """
    bonus = 0.0

    # 按event_type加分
    event_type_str = event.event_type.value if event.event_type else "other"
    if event_type_str == "commitment":
        bonus += 0.15
    elif event_type_str == "preference":
        bonus += 0.10
    elif event_type_str == "boundary":
        bonus += 0.20
    elif event_type_str == "milestone":
        bonus += 0.12

    # 时间锚点加分
    if event.occurred_at and len(event.occurred_at) >= 10:
        bonus += 0.05

    # 内容包含具体对象（非通用词）
    content = event.content
    specific_objects = ["用户", "角色", "我", "你"]  # 可扩展为更精细的对象名
    has_specific_object = any(obj in content for obj in specific_objects)
    if has_specific_object and not _is_generic_summary(content):
        bonus += 0.05

    return bonus


def _merge_and_rank_candidates(
    bm25_events: List[MemoryEvent],
    vector_events: List[MemoryEvent],
    max_results: int = 8,
) -> List[MemoryEvent]:
    """
    合并并排序多路召回结果

    V2升级：显式排序规则优先具体事件而非通用摘要

    排序规则：
    1. 去重（同一event_id只保留最高分数版本）
    2. 特异性加分：commitment/boundary/preference类型优先
    3. 综合分数 = 0.25*相关性 + 0.25*importance + 0.15*freshness + 0.15*activity_score + 0.20*特异性
    4. 通用摘要惩罚：当存在具体事件覆盖同一主题时，摘要降权
    5. activity_score衰减：超过MAX_AGE_DAYS的事件按比例衰减

    Args:
        bm25_events: BM25召回结果
        vector_events: 向量召回结果
        max_results: 最终返回数量

    Returns:
        排序后的MemoryEvent列表
    """
    # 1. 合并去重（含续写事件特殊处理）
    merged = {}
    for event in bm25_events + vector_events:
        # 续写事件特殊处理：提取原始event_id
        base_event_id = event.event_id
        is_continuation = "[续写]" in event.content

        if is_continuation:
            # 续写事件：尝试匹配原始版本
            original_content = event.content.split("[续写]")[0].strip()
            base_event_id = event.event_id or hash(original_content[:50])

        event_key = base_event_id or hash(event.content[:50])

        if event_key not in merged:
            merged[event_key] = event
        else:
            existing = merged[event_key]
            # 续写事件优先替换旧版本（即使分数更低）
            if is_continuation:
                merged[event_key] = event
                logger.debug(f"【续写替换】{event.content[:30]}... 替换旧版本")
            else:
                # 非续写：保留分数更高的版本
                existing_score = getattr(existing, "_bm25_score", 0) + getattr(existing, "_vector_score", 0)
                new_score = getattr(event, "_bm25_score", 0) + getattr(event, "_vector_score", 0)
                if new_score > existing_score:
                    merged[event_key] = event

    candidates = list(merged.values())

    # 2. 检测是否存在具体事件覆盖同一主题
    has_specific_events = any(
        _compute_specificity_bonus(e) > 0.1 for e in candidates
    )

    # 3. 综合排序（含特异性加分）
    def compute_final_score(event: MemoryEvent) -> float:
        # 相关性分数（BM25 + vector）
        relevance = getattr(event, "_bm25_score", 0.5) + getattr(event, "_vector_score", 0.5)
        relevance = min(relevance, 1.0) * 0.25

        # 重要性
        importance = event.importance * 0.25

        # 新鲜度
        try:
            last_seen_time = datetime.fromisoformat(event.last_seen_at.replace("Z", "+00:00"))
            days_ago = (datetime.now() - last_seen_time).days
            freshness = max(0, (MAX_AGE_DAYS - days_ago) / MAX_AGE_DAYS) * 0.15
        except Exception:
            freshness = 0.0

        # activity_score（活跃度衰减）
        activity = event.activity_score * 0.15
        # 超过MAX_AGE_DAYS的事件应用额外衰减
        try:
            last_seen_time = datetime.fromisoformat(event.last_seen_at.replace("Z", "+00:00"))
            days_ago = (datetime.now() - last_seen_time).days
            if days_ago > MAX_AGE_DAYS:
                decay_factor = 1.0 - (days_ago - MAX_AGE_DAYS) / (MAX_AGE_DAYS * 2)
                decay_factor = max(0.1, decay_factor)
                activity *= decay_factor
        except Exception:
            pass

        # 特异性加分
        specificity = _compute_specificity_bonus(event) * 0.20

        # 通用摘要惩罚：当存在具体事件时，通用摘要降权
        if has_specific_events and _is_generic_summary(event.content):
            specificity -= 0.15  # 惩罚分数

        final_score = relevance + importance + freshness + activity + specificity
        return max(0, final_score)  # 确保分数非负

    sorted_candidates = sorted(candidates, key=compute_final_score, reverse=True)

    # 4. 排除低价值摘要（当存在高价值具体事件时）
    if has_specific_events:
        # 过滤掉分数过低的通用摘要
        filtered = []
        for event in sorted_candidates:
            if _is_generic_summary(event.content) and compute_final_score(event) < 0.3:
                logger.debug(f"【V2排序】排除低价值摘要: {event.content[:30]}...")
                continue
            filtered.append(event)
        sorted_candidates = filtered

    # 5. 预算控制
    return sorted_candidates[:max_results]


async def _rerank_candidates(
    query: str,
    events: List[MemoryEvent],
    top_k: int = 4,
) -> List[MemoryEvent]:
    """
    Rerank二次排序

    V2新增：使用FlashrankRerank对合并结果做相关性重估

    Args:
        query: 用户查询文本
        events: 合并后的候选事件
        top_k: 最终返回数量

    Returns:
        Rerank后的事件列表
    """
    if len(events) <= top_k:
        return events

    try:
        from langchain_community.document_compressors import FlashrankRerank
        from langchain_core.documents import Document

        # 转换为Document格式
        documents = [
            Document(page_content=event.content, metadata={"event_id": event.event_id})
            for event in events
        ]

        # FlashrankRerank
        reranker = FlashrankRerank(top_n=top_k)
        compressed = reranker.compress_documents(
            documents=documents,
            query=query,
        )

        # 按rerank结果还原MemoryEvent顺序
        reranked_ids = [doc.metadata.get("event_id") for doc in compressed]
        reranked_events = []
        for event_id in reranked_ids:
            for event in events:
                if event.event_id == event_id:
                    reranked_events.append(event)
                    break

        # 补充：如果有事件没被rerank返回但仍在top_k范围内
        remaining = [e for e in events if e not in reranked_events]
        while len(reranked_events) < top_k and remaining:
            reranked_events.append(remaining.pop(0))

        return reranked_events[:top_k]

    except Exception as e:
        logger.warning(f"Rerank失败: {e}, 使用原始排序")
        return events[:top_k]


async def retrieve_history_events_v2(
    query: str,
    user_id: str,
    llm_id: str,
    filter_metadata: Optional[Dict] = None,
    max_results: int = MAX_HISTORY_EVENTS,
    recent_messages: Optional[List[str]] = None,
    enable_rerank: bool = True,
) -> List[MemoryEvent]:
    """
    历史事件检索V2（阶段5）

    混合检索流程：
    1. BM25关键词召回（memory_bank）
    2. 向量语义检索（Chroma）
    3. 合并去重 + 综合排序（activity_score纳入）
    4. Rerank二次排序
    5. 与最近窗口去重
    6. 预算控制

    Args:
        query: 用户查询文本
        user_id: 用户ID
        llm_id: 模型ID
        filter_metadata: 过滤条件
        max_results: 返回数量上限
        recent_messages: 最近窗口消息（用于去重）
        enable_rerank: 是否启用rerank

    Returns:
        MemoryEvent列表（按综合相关性排序）
    """
    logger.info(f"【V2检索】开始混合检索: query={query[:30]}...")

    # 1. BM25召回
    bm25_events = _bm25_retrieve_from_memory_bank(
        query=query,
        user_id=user_id,
        llm_id=llm_id,
        max_results=10,
    )
    logger.debug(f"【V2检索】BM25召回: {len(bm25_events)} 条")

    # 2. 向量召回
    vector_events = await _vector_retrieve_from_chroma(
        query=query,
        user_id=user_id,
        llm_id=llm_id,
        max_results=10,
    )
    logger.debug(f"【V2检索】向量召回: {len(vector_events)} 条")

    # 3. 合并排序（activity_score纳入）
    merged_events = _merge_and_rank_candidates(
        bm25_events=bm25_events,
        vector_events=vector_events,
        max_results=max_results * 2,  # 给rerank留空间
    )
    logger.debug(f"【V2检索】合并后: {len(merged_events)} 条")

    # 4. Rerank
    if enable_rerank and len(merged_events) > max_results:
        merged_events = await _rerank_candidates(
            query=query,
            events=merged_events,
            top_k=max_results,
        )
        logger.debug(f"【V2检索】Rerank后: {len(merged_events)} 条")

    # 5. 与最近窗口去重
    if recent_messages:
        merged_events = deduplicate_with_recent_window(merged_events, recent_messages)

    # 6. 预算控制
    if len(merged_events) > max_results:
        merged_events = merged_events[:max_results]

    logger.info(f"【V2检索】最终返回: {len(merged_events)} 条")
    return merged_events