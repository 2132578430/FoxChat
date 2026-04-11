# User Profile 动态更新计划

## 背景

当前 `user_profile` 结构（Redis Key: `chat:memory:{user_id}:{llm_id}:user_profile`）包含7个固定维度：
- 核心身份
- 核心性格
- 语言风格
- 互动模式
- 价值观
- 长期兴趣
- 绝对边界

**问题**：在 `_compress_chat_memory` 中进行消息总结时，没有同步更新 `user_profile`

**目标**：在总结时同步更新 `user_profile`，但保持7个维度结构不变

---

## 任务清单

### 1. **创建更新 Prompt 模板** 
- 📄 文件：`app/core/prompts/user_profile_updater.md`
- 🎯 功能：接收当前 `user_profile` + 最近对话历史
- 📤 输出：更新后的 `user_profile`（保持7个维度）
- ⚠️ 关键点：
  - 必须保留所有7个维度
  - 新信息 → 更新对应字段
  - 无新信息 → 保留原值（标记为 `[未提及]` 的除外）

### 2. **实现 Profile 更新 Chain**
- 📝 函数：`_build_profile_updater_chain()`
- 🔧 位置：`app/service/chat_msg_service.py`
- 📋 功能：
  - 构建 LangChain chain
  - 调用 `user_profile_updater.md` 模板
  - 使用 `json_ds_model` 返回 JSON

### 3. **集成到压缩流程**
- 📍 位置：`_compress_chat_memory()` 函数
- 📌 时机：在现有总结流程之后（第256行之后）
- 🔄 流程：
  ```python
  # 现有逻辑
  chain = await _build_summary_chain()
  summary_msg = await chain.ainvoke(...)
  
  # 新增：更新 user_profile
  current_profile = await _get_current_user_profile(user_id, llm_id)
  updated_profile = await _update_user_profile(current_profile, recent_msg_list)
  await _save_user_profile(updated_profile, user_id, llm_id)
  ```

### 4. **实现增量更新策略**
- 🔍 检测：LLM 分析对话是否涉及某个维度
- 📝 更新规则：
  - **有新信息** → 替换/补充该维度
  - **无新信息** → 保留原值（**无论原值是什么**）
- ⚡ 关键点：
  - **必须确保更新的信息是有价值的**，避免"废话对话"覆盖已有画像
  - LLM 需要判断对话中是否提供了关于某个维度的**有意义的新信息**
  - 如果对话是闲聊/无意义内容，**不应该更新任何字段**
  - 即使原值为 `[未提及]`，除非对话中确实提供了该信息，否则保持 `[未提及]`

### 5. **测试验证**
- ✅ 验证 Redis 中的 `user_profile` 结构完整性
- ✅ 确保7个维度都被保留
- ✅ 检查更新逻辑的正确性

---

## 技术细节

### Redis Key 格式
```
chat:memory:{user_id}:{llm_id}:user_profile
```

### 更新时机
- `_compress_chat_memory()` 被调用时
- 即：累积30条消息后触发压缩时

### LLM 模型
- 使用 `json_ds_model` 确保输出 JSON 格式

---

## 文件清单

| 操作 | 文件路径 |
|------|---------|
| 新建 | `app/core/prompts/user_profile_updater.md` |
| 修改 | `app/service/chat_msg_service.py` |

---

## 状态

- ⏳ 待开始
- 📅 计划创建时间：2026-04-10
