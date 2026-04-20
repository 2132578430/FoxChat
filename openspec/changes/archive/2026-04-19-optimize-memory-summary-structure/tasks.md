## 1. 配置变量定义

- [x] 1.1 在 `memory_summary_service.py` 中定义 `RECENT_MSG_KEEP_SIZE = 10`
- [x] 1.2 在 `memory_summary_service.py` 中定义 `SUMMARY_TRIGGER_THRESHOLD = 14`
- [x] 1.3 确保变量关系正确：`SUMMARY_TRIGGER_THRESHOLD > RECENT_MSG_KEEP_SIZE`

## 2. 触发条件修改

- [x] 2.1 修改 `async_summary_msg` 函数，将硬编码 `12` 改为 `SUMMARY_TRIGGER_THRESHOLD`
- [x] 2.2 修改 `lrange(9, -1)` 为 `lrange(RECENT_MSG_KEEP_SIZE, -1)`
- [x] 2.3 修改 `ltrim(0, 9)` 为 `ltrim(0, RECENT_MSG_KEEP_SIZE - 1)`
- [x] 2.4 添加边界检查日志，确保无遗漏

## 3. 提示词修改

- [x] 3.1 修改 `memory_event_extractor.md`，增加 `actor` 字段要求
- [x] 3.2 在提示词中明确 USER/AI 区分规则
- [x] 3.3 添加 `action` 字段说明（可选）
- [x] 3.4 添加 `keywords` 字段说明（可选，为后续预留）

## 4. 事件解析修改

- [x] 4.1 修改 `_extract_memory_events` 函数，验证 `actor` 字段存在
- [x] 4.2 为缺少 `actor` 的事件添加默认值处理
- [x] 4.3 更新事件存储格式，确保 JSON 结构正确

## 5. 测试验证

- [x] 5.1 测试触发条件：确认 14 条消息触发总结（代码逻辑验证）
- [x] 5.2 测试边界：确认 recent_msg 保留 10 条，总结 4 条（代码逻辑验证）
- [x] 5.3 测试事件结构：确认 actor 字段正确提取（代码逻辑验证）
- [x] 5.4 测试无遗漏：确认消息不会丢失（代码逻辑验证）