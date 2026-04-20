### Requirement: AI对用户的称呼自动生成

当用户在初始记忆中提供正式姓名时，系统 SHALL 自动生成合适的昵称作为"AI对用户的称呼"。

#### Scenario: 用户提供正式姓名
- **WHEN** 用户输入"我叫李炳旭"或"我是王小明"
- **THEN** user_profile.爱称.AI对用户的称呼 SHALL 为简短昵称（如"炳旭"、"小明"），而非"[未提及]"

#### Scenario: 用户明确指定爱称
- **WHEN** 用户输入"我叫李炳旭，你可以叫我阿炳"
- **THEN** user_profile.爱称.AI对用户的称呼 SHALL 为用户指定的爱称"阿炳"

#### Scenario: 用户未提供任何姓名
- **WHEN** 用户输入中完全没有姓名信息
- **THEN** user_profile.爱称.AI对用户的称呼 SHALL 为"[未提及]"

### Requirement: 移除character_card的爱称字段

character_card 结构 SHALL 不包含"爱称"字段，避免与 user_profile.爱称 重复。

#### Scenario: 生成角色卡时无爱称字段
- **WHEN** 系统生成 character_card
- **THEN** character_card SHALL 不包含"爱称"字段

#### Scenario: 旧数据兼容处理
- **WHEN** 系统读取已存储的旧 character_card 数据（包含"爱称"字段）
- **THEN** 系统 SHALL 正常处理，不因"爱称"字段存在而报错

### Requirement: 统一的爱称注入逻辑

系统 SHALL 只使用 user_profile.爱称 来生成 call_convention，不使用 character_card.爱称。

#### Scenario: call_convention生成
- **WHEN** 系统构建对话提示词
- **THEN** call_convention SHALL 只基于 user_profile.爱称.用户对AI的称呼 和 user_profile.爱称.AI对用户的称呼