## Context

当前系统在初始记忆处理时生成两个包含"爱称"的结构：
- `character_card`（角色卡）：包含"爱称"数组，表示用户对AI的称呼
- `user_profile`（用户画像）：包含"爱称"对象，有"用户对AI的称呼"和"AI对用户的称呼"两个字段

问题：
1. 两个"爱称"字段语义重叠，`character_card.爱称` ≈ `user_profile.爱称.用户对AI的称呼`
2. `character_card.爱称` 只是描述性文本，没有明确指令；真正起作用的是 `user_profile.爱称` 通过 `call_convention` 注入
3. `user_profile.md` 的"AI对用户的称呼"字段，当用户提供正式姓名时标记 `[未提及]`，不会自动生成昵称
4. `character_card.md` 已有"正式名字→自动生成昵称"逻辑，但 `user_profile.md` 没有

## Goals / Non-Goals

**Goals:**
- 统一爱称逻辑，只保留 `user_profile.爱称`
- 让"AI对用户的称呼"支持"正式名字→自动生成昵称"
- 移除 `character_card.爱称` 字段及相关处理代码

**Non-Goals:**
- 不改变 `call_convention` 的注入方式
- 不改变其他记忆结构（core_anchor、memory_bank 等）

## Decisions

### 决策1：修改 `user_profile.md` prompt

**方案**：在"爱称"字段说明中添加"正式名字→自动生成昵称"的逻辑

**理由**：与 `character_card.md` 保持一致的逻辑，当用户提供正式姓名（如"李炳旭"）时，模型应自动生成合适的昵称（如"炳旭"）

**修改内容**：
```
"AI对用户的称呼": "AI在对话中习惯使用的对用户的称呼。
  - 如果原文明确提及爱称，使用原文的爱称
  - 如果原文只提供正式姓名（如李炳旭、王小明），自动生成简短昵称（如炳旭、小明）
  - 如果原文完全没有姓名信息，标注[未提及]"
```

### 决策2：移除 `character_card.md` 的"爱称"字段

**方案**：从 prompt 中删除"爱称"字段及相关说明

**理由**：避免与 `user_profile.爱称` 重复，减少混淆

### 决策3：移除 `chat_msg_service.py` 中对 `character_card.爱称` 的处理

**方案**：删除第192-193行的处理逻辑

**理由**：`character_card.爱称` 已移除，无需处理

## Risks / Trade-offs

- **风险**：已存储的旧 `character_card` 数据可能仍有"爱称"字段
  - **缓解**：代码中已有 `if character_card.get("爱称")` 判断，空数组或不存在时不会处理，向后兼容

- **风险**：模型可能生成不合适的昵称
  - **缓解**：prompt 中明确"简短昵称"的要求，避免过度创意