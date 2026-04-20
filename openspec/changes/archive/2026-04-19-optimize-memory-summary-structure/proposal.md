## Why

当前记忆系统存在三个核心问题：

1. **记忆压缩导致主体归属丢失**：事件提取时缺少 `actor` 字段，导致"谁做了什么"不明确。例如"很开心，可以洗漱了"丢失了【用户】要去洗漱的主体信息。

2. **总结触发条件硬编码**：当前使用 `>= 12` 条消息和 `lrange(9, -1)` 等硬编码值，可维护性差，无法灵活调整记忆窗口大小。

3. **记忆边界不清晰**：总结频率和保留条数的关系不明确，可能导致记忆遗漏或重复。

## What Changes

- 修改事件提取提示词，增加 `actor` 字段，明确主体归属（USER/AI）
- 将总结触发条件改为基于对话轮数（7 轮 = 14 条消息）
- 将硬编码边界值改为可配置变量（`RECENT_MSG_KEEP_SIZE`、`SUMMARY_TRIGGER_THRESHOLD`）
- 确保记忆边界清晰：recent_msg 保留最近 N 条，总结范围为第 N+1 条及之后

## Capabilities

### New Capabilities

- `structured-memory-event`: 结构化记忆事件格式，包含 actor、action、keywords 等字段

### Modified Capabilities

- `memory-summary-trigger`: 修改总结触发条件，从消息条数改为对话轮数，并使用变量控制边界

## Impact

- `memory_summary_service.py`: 修改触发条件和边界逻辑
- `memory_event_extractor.md`: 修改提示词，增加 actor 字段要求
- `chat_msg_service.py`: 可能需要调整 recent_msg 的读取逻辑（如果保留条数变化）