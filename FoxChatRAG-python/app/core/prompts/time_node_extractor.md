# 时间节点候选提取规则（阶段3第一版）

本文档定义 summary 流程中时间节点候选的提取规则。第一版采用 rule-based 提取，不依赖 LLM 自由抽取。

## 创建条件

**必须创建节点的情况：**

| 输入模式 | created_from | 说明 |
|---------|--------------|------|
| 用户明确未来事件："明天考试"、"下周出结果" | `user_future_event` | 未来时间 + 事项本体明确 |
| 用户明确要求跟进："明天提醒我问结果"、"明天继续聊" | `user_future_followup` | 未来时间 + 跟进行为明确 |
| AI 明确承诺："明天我再陪你看结果" | `ai_commitment` | 需要保证后续可召回 |

**不创建节点的情况：**

| 输入模式 | 原因 |
|---------|------|
| 纯过去经历："我昨天很难过" | 这是历史信息，不是未来节点 |
| 模糊长期时间："以后再说"、"改天吧" | 时间锚点过弱，第一版不做 |
| 当前轮局部拒绝："今天不想聊这个" | 属于当前状态，不是 time node |

## 时间归一化规则

| 原始表达 | due_at | precision | 处理原则 |
|---------|--------|-----------|---------|
| 明天 | 次日日期 | `day` | 不伪造具体小时 |
| 后天 | 后两日日期 | `day` | 只保留日期精度 |
| 下周 | 下一个自然周的锚点日期 | `day` | 固定使用周起始日期 |
| 今晚8点 | 具体 datetime | `datetime` | 保留明确时间 |
| 明天下午 | 次日日期 | `day` | 第一版降级为 day 精度 |

**归一化总原则：**
- 第一版只稳定支持 `day` 与 `datetime`
- 无法高置信归一化时，优先不建节点，而不是伪造时间
- 同类表达必须固定统一口径，避免解析不稳定

## 提取流程

```text
summary batch 文本
    ↓
扫描时间关键词（明天/后天/下周/今晚X点）
    ↓
判断是否满足创建条件（未来事件/跟进请求/AI承诺）
    ↓
归一化时间表达 → due_at
    ↓
构建 TimeNodeCandidate
    ↓
设置路由元数据（why_routed, change_type）
    ↓
返回候选列表
```

## 规则代码示例

```python
# 时间表达匹配
TIME_EXPRESSIONS = {
    "明天": timedelta(days=1),
    "后天": timedelta(days=2),
    "下周": timedelta(weeks=1),
}

# 未来事项关键词
FUTURE_EVENT_KEYWORDS = ["考试", "出结果", "面试", "复查", "见面", "约会"]
FUTURE_FOLLOWUP_KEYWORDS = ["提醒", "继续聊", "再聊", "跟进"]
AI_COMMITMENT_PATTERNS = ["明天再", "下次再聊", "之后再", "稍后给你"]

def extract_time_node_candidates_from_summary(summary_text: str, current_round: int) -> List[TimeNodeCandidate]:
    candidates = []
    
    # 扫描时间关键词
    for keyword in TIME_EXPRESSIONS.keys():
        if keyword in summary_text:
            # 判断是否满足创建条件
            created_from = determine_created_from(summary_text, keyword)
            if created_from:
                due_at = normalize_time(keyword)
                content = extract_content(summary_text, keyword)
                
                candidate = TimeNodeCandidate(
                    content=content,
                    time_expression=keyword,
                    due_at=due_at,
                    precision="day",
                    created_from=created_from,
                    source_round=current_round,
                    is_valid_time=True,
                    why_routed=f"检测到未来时间表达 '{keyword}' + {created_from}",
                )
                candidates.append(candidate)
    
    return candidates
```

## 与 runtime 的关系

summary 来源的 time_node 候选与 runtime 来源共享相同的存储和激活机制。

- summary 来源：补充 runtime 可能漏掉的未来事项
- 去重规则：按 content 语义相似度去重，避免重复建节点
- 激活机制：复用 `time_node_service.py` 的到期检查与激活逻辑

## 输出格式

时间节点候选输出为 `TimeNodeCandidate` 对象列表：

```json
[
  {
    "content": "跟进考试结果",
    "time_expression": "明天",
    "due_at": "2024-01-16",
    "precision": "day",
    "created_from": "user_future_followup",
    "change_type": "new_entry",
    "target_layer": "time_node",
    "why_routed": "检测到未来时间表达 '明天' + user_future_followup",
    "source_round": 42,
    "is_valid_time": true
  }
]
```

## 第一版限制

- 不处理复杂时间表达（如"出成绩那天"、"面试结束后"）
- 不处理模糊长期表达（如"以后"、"改天"）
- 不引入循环提醒、后台主动调度、多时区支持
- 不建立完整时间线系统，只解决最小跨日跟进能力