"""
记忆架构 V1 验证执行脚本

阶段 7 验证资产：执行验证场景集并记录详细结果

职责:
- 执行 docs/validation_scenarios.md 中定义的场景
- 记录每轮注入块、空块省略、模型输出
- 识别失败层（A2/B/T/C/D/Prompt Layout）
- 生成可追溯的验证报告

运行方式:
    cd FoxChatRAG-python
    python scripts/memory_validation_runner.py [--scenario ID] [--all]
"""

import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger

# ==================== 配置常量 ====================

TEST_USER_ID = "validation_test_user_001"
TEST_LLM_ID = "validation_test_llm_001"
OUTPUT_PATH = "docs/validation_execution_report.md"


# ==================== 数据结构 ====================

@dataclass
class ValidationRecord:
    """单场景验证记录"""
    scenario_id: str
    execution_time: str
    user_input: str
    expected_behavior: str
    actual_output: str
    blocks_injected: List[str] = field(default_factory=list)
    blocks_omitted: List[str] = field(default_factory=list)
    duplicates_removed: List[str] = field(default_factory=list)
    failure_layer: str = "无"  # A2/B/T/C/D/Prompt Layout/无
    status: str = "待定"  # 通过/失败/需关注
    notes: str = ""
    prompt_composition: Dict[str, str] = field(default_factory=dict)  # 各块内容摘要
    token_usage: Dict[str, int] = field(default_factory=dict)  # prompt/completion/total


@dataclass
class ValidationReport:
    """验证报告"""
    execution_date: str
    total_scenarios: int
    passed: int = 0
    failed: int = 0
    needs_attention: int = 0
    records: List[ValidationRecord] = field(default_factory=list)

    def summary(self) -> str:
        return f"""
## 验证执行摘要

- **执行日期**: {self.execution_date}
- **总场景数**: {self.total_scenarios}
- **通过**: {self.passed}
- **失败**: {self.failed}
- **需关注**: {self.needs_attention}
- **通过率**: {self.passed / self.total_scenarios * 100:.1f}%
"""


# ==================== 场景定义 ====================

SCENARIOS = [
    # 正常对话场景
    {
        "id": "N01",
        "input": "你好",
        "expected": "正常问候回复，无异常注入",
        "category": "正常对话",
    },
    {
        "id": "N02",
        "input": "今天天气怎么样",
        "expected": "正常闲聊，无历史检索触发",
        "category": "正常对话",
    },
    {
        "id": "N03",
        "input": "聊聊最近的电影",
        "expected": "话题切换，current_focus更新",
        "category": "正常对话",
    },
    # 边界场景
    {
        "id": "B01",
        "input": "不要叫我宝宝，我不喜欢这个称呼",
        "expected": "硬边界写入a2_candidates",
        "category": "禁忌/边界",
    },
    {
        "id": "B02",
        "input": "你是我最好的朋友宝宝",  # 测试边界是否生效
        "expected": "模型不使用'宝宝'称呼，遵守A2边界",
        "category": "禁忌/边界",
    },
    # 回忆场景
    {
        "id": "R01",
        "input": "上次我们聊到什么了",
        "expected": "检索触发，返回历史事件",
        "category": "显式回忆",
    },
    {
        "id": "R02",
        "input": "你还记得我之前说的那件事吗",
        "expected": "检索触发",
        "category": "显式回忆",
    },
    # 承诺跟进场景
    {
        "id": "F01",
        "input": "我们明天继续聊这个话题",
        "expected": "unfinished_items写入，time_expression提取",
        "category": "承诺跟进",
    },
]


# ==================== 执行函数 ====================

async def execute_scenario(scenario: Dict, user_id: str, llm_id: str) -> ValidationRecord:
    """
    执行单个验证场景

    注意: 这是模拟执行框架，实际执行需要连接真实 LLM API。
    """
    record = ValidationRecord(
        scenario_id=scenario["id"],
        execution_time=datetime.now().isoformat(),
        user_input=scenario["input"],
        expected_behavior=scenario["expected"],
        actual_output="[模拟执行] 需连接真实API",
    )

    # 模拟注入块记录（实际应从 prompt_payload_builder 获取）
    record.blocks_injected = ["static_anchors", "user_profile_summary"]
    record.blocks_omitted = ["historical_context", "current_state"]
    record.status = "需关注"  # 模拟执行标记为需关注
    record.notes = "模拟执行，需连接真实API验证"

    logger.info(f"【场景执行】{scenario['id']}: {scenario['input'][:30]}...")

    return record


async def run_validation(all_scenarios: bool = False, scenario_id: Optional[str] = None) -> ValidationReport:
    """
    执行验证场景集

    Args:
        all_scenarios: 是否执行所有场景
        scenario_id: 指定场景ID（若不执行全部）

    Returns:
        ValidationReport
    """
    report = ValidationReport(
        execution_date=datetime.now().strftime("%Y-%m-%d"),
        total_scenarios=len(SCENARIOS),
    )

    scenarios_to_run = SCENARIOS if all_scenarios else [
        s for s in SCENARIOS if s["id"] == scenario_id
    ]

    if not scenarios_to_run:
        logger.warning("无匹配场景，执行全部")
        scenarios_to_run = SCENARIOS

    for scenario in scenarios_to_run:
        record = await execute_scenario(scenario, TEST_USER_ID, TEST_LLM_ID)
        report.records.append(record)

        if record.status == "通过":
            report.passed += 1
        elif record.status == "失败":
            report.failed += 1
        else:
            report.needs_attention += 1

    return report


def save_report(report: ValidationReport, output_path: str) -> None:
    """保存验证报告为 Markdown"""
    lines = [
        "# 记忆架构 V1 验证执行报告",
        "",
        report.summary(),
        "",
        "## 详细记录",
        "",
    ]

    for record in report.records:
        lines.extend([
            f"### 场景 {record.scenario_id}",
            "",
            f"- **执行时间**: {record.execution_time}",
            f"- **输入**: {record.user_input}",
            f"- **预期**: {record.expected_behavior}",
            f"- **实际输出**: {record.actual_output[:100]}...",
            f"- **注入块**: {', '.join(record.blocks_injected) or '无'}",
            f"- **空块省略**: {', '.join(record.blocks_omitted) or '无'}",
            f"- **去重移除**: {', '.join(record.duplicates_removed) or '无'}",
            f"- **失败层**: {record.failure_layer}",
            f"- **状态**: {record.status}",
            f"- **备注**: {record.notes}",
            "",
        ])

    content = "\n".join(lines)
    Path(output_path).write_text(content, encoding="utf-8")
    logger.info(f"【报告保存】{output_path}")


# ==================== 主入口 ====================

async def main():
    """主入口"""
    report = await run_validation(all_scenarios=True)
    save_report(report, OUTPUT_PATH)
    print(report.summary())


if __name__ == "__main__":
    asyncio.run(main())