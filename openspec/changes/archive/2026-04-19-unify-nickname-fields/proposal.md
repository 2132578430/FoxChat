## Why

当前系统存在两个"爱称"字段：`character_card.爱称` 和 `user_profile.爱称`，导致逻辑重复和混乱。更严重的是，当用户输入正式姓名（如"李炳旭"）时，`user_profile.爱称.AI对用户的称呼` 被标记为 `[未提及]`，模型不知道该如何称呼用户，导致对话中直接使用全名，体验生硬。

## What Changes

- **修改 `user_profile.md` prompt**：让"AI对用户的称呼"字段支持"正式名字→自动生成昵称"的逻辑
- **修改 `character_card.md` prompt**：移除"爱称"字段，避免与 `user_profile` 重复
- **修改 `chat_msg_service.py`**：移除对 `character_card.爱称` 的处理逻辑

## Capabilities

### New Capabilities

- `nickname-generation`: 统一的爱称生成逻辑，当用户提供正式姓名时自动生成合适的昵称

### Modified Capabilities

- 无（这是新增功能，不改变现有 spec 的需求）

## Impact

- `FoxChatRAG-python/app/core/prompts/user_profile.md` - 修改 prompt
- `FoxChatRAG-python/app/core/prompts/character_card.md` - 移除爱称字段
- `FoxChatRAG-python/app/core/prompts/user_profile_updater.md` - 同步修改更新逻辑
- `FoxChatRAG-python/app/service/chat_msg_service.py` - 移除 character_card.爱称 的处理