## Context

当前记忆系统的核心流程：

```
对话 → lpush 到 recent_msg → 检查条数 >= 12 → 触发总结
                                    │
                                    ▼
                    lrange(9, -1) 取出第 11 条及之后
                    ltrim(0, 9) 保留前 10 条
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
              总结 → Chroma              提取事件 → Memory Bank (Redis)
```

当前事件结构缺少 `actor` 字段，导致主体归属不明确。边界值硬编码在代码中，可维护性差。

## Goals / Non-Goals

**Goals:**

- 明确事件主体归属（USER/AI）
- 将硬编码边界值改为可配置变量
- 确保记忆边界清晰，无遗漏

**Non-Goals:**

- 关键词提取机制（后续变更）
- Chroma 存储结构优化（后续变更）
- Multi-agent 审查机制（后续变更）

## Decisions

### 1. 总结频率：7 轮对话（14 条消息）

**选择**：`SUMMARY_TRIGGER_THRESHOLD = 14`

**理由**：
- 6 轮（12 条）太频繁，总结开销大
- 10 轮（20 条）太长，recent_msg 会积累过多
- 7 轮是平衡点，既保证及时总结，又不会让 recent_msg 过长

**备选方案**：
- 6 轮：更及时，但开销更大
- 10 轮：开销小，但 recent_msg 可能积累到 20+ 条

### 2. 保留条数：10 条消息

**选择**：`RECENT_MSG_KEEP_SIZE = 10`

**理由**：
- 10 条消息 ≈ 5 轮对话，足够覆盖最近的上下文
- 与 `SUMMARY_TRIGGER_THRESHOLD = 14` 形成清晰边界
- 边界关系：`SUMMARY_TRIGGER_THRESHOLD > RECENT_MSG_KEEP_SIZE`

### 3. 结构化事件格式

**选择**：增加 `actor` 字段

```json
{
  "time": "2026-04-19",
  "type": "event",
  "actor": "USER",
  "action": "go_to_wash",
  "content": "用户要去洗漱了",
  "keywords": ["洗漱", "睡前"]
}
```

**理由**：
- `actor` 明确主体归属，解决"谁做了什么"的问题
- `action` 可选，用于后续关键词机制
- `keywords` 可选，为后续 SillyTavern 关键词机制预留

**备选方案**：
- 只在 content 中标注："[USER] 用户要去洗漱了" - 不够结构化，后续检索困难

### 4. 变量命名

**选择**：

```python
RECENT_MSG_KEEP_SIZE = 10
SUMMARY_TRIGGER_THRESHOLD = 14
```

**理由**：
- 语义清晰，易于理解
- 可在配置文件中调整

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| 7 轮总结可能导致 recent_msg 积累到 14 条 | 监控 Token 消耗，必要时调整阈值 |
| actor 字段提取可能不准确 | 设计清晰的提示词，明确 USER/AI 区分 |
| 变量调整需要重启服务 | 后续可考虑动态配置 |

## Migration Plan

1. 修改 `memory_summary_service.py`：添加变量，修改触发条件
2. 修改 `memory_event_extractor.md`：增加 actor 字段要求
3. 测试验证：确保边界清晰，无遗漏
4. 无需数据迁移：现有记忆格式兼容