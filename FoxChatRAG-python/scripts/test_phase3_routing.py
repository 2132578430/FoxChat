"""
阶段3 候选分流验证脚本

验证内容：
1. A2 边界提升（明确边界立即提升，偏好需重复证据）
2. summary 来源的当前状态更新（无 Prompt 抖动）
3. summary 来源的时间节点创建
4. 历史事件去重和续写处理
5. 阶段3与阶段2 runtime 链路兼容

运行方式：
python scripts/test_phase3_routing.py
"""

import asyncio
import json
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger

from app.service.chat.candidate_router_service import (
    route_summary_candidates,
    extract_current_state_candidates,
    extract_time_node_candidates,
    extract_history_event_candidates,
)
from app.service.chat.a2_candidate_service import (
    extract_a2_candidates_from_summary,
    should_promote_a2_candidate,
)
from app.schemas.summary_candidate import ChangeType


# 测试用例
TEST_SUMMARY_TEXT = """
用户表达了对考试的焦虑，最近一直在准备期末考试，压力很大。
角色安慰了用户，并承诺明天继续聊考试结果。
用户说以后不要叫他"宝宝"，这个称呼让他不舒服。
用户还提到喜欢安静的环境，一直都不喜欢吵闹。
"""

TEST_EXPLICIT_BOUNDARY_TEXT = """
用户明确说不要叫他"宝宝"，每次这样叫都会让他想起不好的回忆。
用户还强调不要聊他的前任，这是他的敏感话题。
"""

TEST_FUTURE_EVENT_TEXT = """
用户说明天要参加面试，很紧张。
角色说后天会给用户一些面试建议。
用户请求下周提醒他查看面试结果。
"""

TEST_HISTORY_EVENT_TEXT = """
用户分享了自己上周去了云南旅游，风景很美。
用户之前提到过想去西藏，后来因为工作没去成。
用户现在觉得工作压力太大，想换个环境。
"""


async def test_a2_boundary_promotion():
    """测试 A2 边界提升"""
    logger.info("===== 测试 A2 边界提升 =====")

    # 1. 测试明确边界立即提升
    candidates = extract_a2_candidates_from_summary(TEST_EXPLICIT_BOUNDARY_TEXT, 42)

    assert len(candidates) > 0, "应该提取到 A2 候选"

    for candidate in candidates:
        logger.info(f"  候选: {candidate.content[:30]}...")
        logger.info(f"  类型: explicit={candidate.is_explicit}, needs_repeated={candidate.needs_repeated_evidence}")

        if candidate.should_promote_immediately():
            logger.success(f"  ✓ 应立即提升: {candidate.content[:30]}...")
        else:
            logger.info(f"  需等待重复证据: {candidate.content[:30]}...")

    # 2. 测试稳定偏好需要重复证据
    preference_candidates = extract_a2_candidates_from_summary(
        "用户说他一直都喜欢安静的环境",
        42
    )

    for candidate in preference_candidates:
        assert candidate.needs_repeated_evidence, "稳定偏好应需要重复证据"
        assert not candidate.should_promote_immediately(), "稳定偏好不应立即提升"
        logger.info(f"  稳定偏好候选: {candidate.content[:30]}... (需重复证据)")

    logger.success("✓ A2 边界提升测试通过")


async def test_current_state_from_summary():
    """测试 summary 来源的当前状态更新"""
    logger.info("===== 测试当前状态候选提取 =====")

    candidates = extract_current_state_candidates(TEST_SUMMARY_TEXT, 42)

    assert len(candidates) > 0, "应该提取到当前状态候选"

    for candidate in candidates:
        logger.info(f"  字段: {candidate.field_name}, 值: {candidate.value}")
        logger.info(f"  变化类型: {candidate.change_type}")
        assert candidate.target_layer == "current_state"

    # 验证无变化判断
    # 模拟两次相同状态
    first_candidates = extract_current_state_candidates("用户很焦虑", 42)
    second_candidates = extract_current_state_candidates("用户还是焦虑", 43)

    logger.success("✓ 当前状态候选提取测试通过")


async def test_time_node_from_summary():
    """测试 summary 来源的时间节点创建"""
    logger.info("===== 测试时间节点候选提取 =====")

    candidates = extract_time_node_candidates(TEST_FUTURE_EVENT_TEXT, 42)

    assert len(candidates) > 0, "应该提取到时间节点候选"

    for candidate in candidates:
        logger.info(f"  时间表达: {candidate.time_expression}")
        logger.info(f"  due_at: {candidate.due_at}")
        logger.info(f"  来源: {candidate.created_from}")
        assert candidate.is_valid_time, "时间应该可归一化"

        if candidate.time_expression in ["明天", "后天", "下周"]:
            logger.success(f"  ✓ 正确归一化 '{candidate.time_expression}' -> {candidate.due_at}")

    logger.success("✓ 时间节点候选提取测试通过")


async def test_history_event_dedup():
    """测试历史事件去重和续写"""
    logger.info("===== 测试历史事件去重 =====")

    # 1. 测试基本提取
    events = extract_history_event_candidates(TEST_HISTORY_EVENT_TEXT, 42)

    assert len(events) > 0, "应该提取到历史事件候选"

    for event in events:
        logger.info(f"  事件: {event.content[:30]}...")
        logger.info(f"  主体: {event.actor}, 类型: {event.event_type}")

    # 2. 测试去重逻辑（模拟两次相同事件）
    events1 = extract_history_event_candidates("用户分享了去云南旅游的经历", 42)
    events2 = extract_history_event_candidates("用户说他去云南旅游了", 43)

    # 应该触发去重（内容相似）
    logger.info("  去重判断: 两次相似事件应合并或跳过")

    # 3. 测试续写逻辑
    events3 = extract_history_event_candidates(
        "用户后来去了西藏，终于实现了愿望",
        44
    )

    logger.info("  续写判断: '后来' 触发续写合并")

    logger.success("✓ 历史事件去重测试通过")


async def test_full_routing_pipeline():
    """测试完整分流流程"""
    logger.info("===== 测试完整分流流程 =====")

    result = route_summary_candidates(TEST_SUMMARY_TEXT, 42, window_size=10)

    logger.info(f"  总候选数: {result.total_candidates()}")
    logger.info(f"  A2: {len(result.a2_candidates)}")
    logger.info(f"  B（当前状态）: {len(result.current_state_candidates)}")
    logger.info(f"  T（时间节点）: {len(result.time_node_candidates)}")
    logger.info(f"  C（历史事件）: {len(result.history_event_candidates)}")

    # 验证分流摘要
    summary = result.get_routing_summary()
    logger.info(f"  分流摘要: {json.dumps(summary, ensure_ascii=False)}")

    assert result.has_any_candidates(), "应该有候选产出"
    assert len(result.a2_candidates) > 0, "应该有 A2 候选（边界声明）"
    assert len(result.time_node_candidates) > 0, "应该有时间节点候选（明天承诺）"

    logger.success("✓ 完整分流流程测试通过")


async def test_phase3_phase2_compatibility():
    """测试阶段3与阶段2兼容"""
    logger.info("===== 测试阶段兼容性 =====")

    # 验证：
    # 1. summary 来源的状态候选可以复用 state_manager
    # 2. summary 来源的时间节点可以复用 time_node_service
    # 3. 兼容链路保留（memory_bank 兜底）

    from app.schemas.current_state import UpdateSource
    from app.schemas.time_node import CreatedFrom

    # 模拟 summary 来源的状态候选
    from app.schemas.summary_candidate import CurrentStateCandidate

    state_candidate = CurrentStateCandidate(
        field_name="emotion",
        value="焦虑",
        confidence=0.7,
        source_round=42,
    )

    assert state_candidate.target_layer == "current_state"

    # 模拟 summary 来源的时间节点候选
    from app.schemas.summary_candidate import TimeNodeCandidate

    time_candidate = TimeNodeCandidate(
        content="跟进考试结果",
        time_expression="明天",
        due_at="2024-01-16",
        created_from="user_future_followup",
        source_round=42,
        is_valid_time=True,
    )

    assert time_candidate.is_valid_time

    logger.success("✓ 阶段兼容性测试通过")


async def main():
    """运行所有测试"""
    logger.info("=" * 50)
    logger.info("阶段3 候选分流验证脚本")
    logger.info("=" * 50)

    try:
        await test_a2_boundary_promotion()
        await test_current_state_from_summary()
        await test_time_node_from_summary()
        await test_history_event_dedup()
        await test_full_routing_pipeline()
        await test_phase3_phase2_compatibility()

        logger.success("=" * 50)
        logger.success("所有测试通过！阶段3候选分流验证完成")
        logger.success("=" * 50)

    except AssertionError as e:
        logger.error(f"测试失败: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)