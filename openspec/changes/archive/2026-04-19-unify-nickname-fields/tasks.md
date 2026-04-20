## 1. Prompt修改

- [x] 1.1 修改 `user_profile.md`：更新"AI对用户的称呼"字段说明，添加"正式名字→自动生成昵称"逻辑
- [x] 1.2 修改 `user_profile_updater.md`：同步更新"AI对用户的称呼"字段说明
- [x] 1.3 修改 `character_card.md`：移除"爱称"字段及相关说明（第54行、第74行说明）

## 2. 代码修改

- [x] 2.1 修改 `chat_msg_service.py`：移除第192-193行对 `character_card.爱称` 的处理逻辑

## 3. 验证

- [ ] 3.1 测试：输入"我叫李炳旭"，验证 user_profile.爱称.AI对用户的称呼 不为"[未提及]"
- [ ] 3.2 测试：验证 character_card 不包含"爱称"字段
- [ ] 3.3 测试：验证 call_convention 正常生成