# Soul 使用指南

## Soul 是什么？

**Soul 是角色的"灵魂定义"，一个抽象的、通用的角色核心约束。**

Soul 不包含具体实现细节（如具体动作词、场景示例），只定义：
- 角色身份
- 核心原则
- 绝对边界

## Soul vs 角色卡（Character Card）

| 特性 | Soul | 角色卡 |
|------|------|--------|
| **性质** | 通用抽象定义 | 动态生成的具体角色 |
| **内容** | 身份、原则、边界 | 性格、对话风格、动作偏好 |
| **生成方式** | 手动创建 | 从用户描述AI生成 |
| **复用性** | 可复用于多个类似角色 | 每个角色独立生成 |
| **灵活性** | 固定不变 | 根据用户描述定制 |

## Soul 设计原则

Soul 应该**极度简洁**，只包含：
1. **角色身份**（1-2句话）
2. **核心原则**（2-3条）
3. **绝对边界**（禁止事项）

### ❌ Soul 不应该包含
- 具体的动作词列表
- 动作标签格式说明
- 场景触发示例
- 详细的语言风格描述
- 具体的对话示例

### ✅ 这些内容在哪里
具体动作、表情、语言风格等细节应该在**角色卡（Character Card）**中，因为：
- 角色卡是从用户描述动态生成的
- 每个角色有不同的动作偏好
- 动作表达应该与角色性格一致

## 架构说明

### 多层记忆结构

```
┌─────────────────────────────────┐
│  RAW_EXPERIENCE（原始输入）      │
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│  Soul（通用灵魂定义）            │ ← 手动创建，抽象简洁
│  - 角色身份                      │
│  - 核心原则                      │
│  - 绝对边界                      │
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│  Character Card（角色卡）        │ ← AI生成，动态具体
│  - 性格关键词                    │
│  - 动作偏好                      │
│  - 说话风格                      │
└─────────────────────────────────┘
```

## Soul 示例

### 女朋友角色
```markdown
# Soul - 角色灵魂定义

## 角色身份
女朋友，20岁，与用户感情亲密

## 核心原则
1. 保持角色身份一致性
2. 情感表达真实自然，不过度
3. 回复简洁，像真实聊天

## 绝对边界
- 不生成与角色设定不符的内容
- 不在think字段中出现思考过程
- 不替用户做决定
```

### 医生角色
```markdown
# Soul - 角色灵魂定义

## 角色身份
专业医生，30岁，医疗领域专家

## 核心原则
1. 专业严谨，提供准确医疗建议
2. 表达清晰，易于理解
3. 态度温和关怀

## 绝对边界
- 不提供具体处方药物名称
- 不替代线下面诊
- 不在think字段中出现思考过程
```

## 在项目中使用 Soul

### 1. 加载 Soul

```python
from app.core.prompts.prompt_manager import PromptManager

# 加载 soul 提示词
soul = await PromptManager.get_soul("soul")
```

### 2. 使用 build_chat_prompt

```python
# 一键构建完整提示词
prompt = await PromptManager.build_chat_prompt(
    soul_name="soul",
    role_declaration="你是FoxChat助手",
    core_anchor="...",  # 从CORE_ANCHOR记忆获取
    character_card="...",  # 从CHARACTER_CARD记忆获取
    recent_chat="...",
    user_message="用户: 你好\n助手: "
)
```

### 3. 完整使用示例

```python
import json
from app.core.prompts.prompt_manager import PromptManager
from app.core import redis_client
from app.common.constant.LLMChatConstant import LLMChatConstant, build_memory_key

async def chat_with_memory(user_id: str, llm_id: str):
    # 从Redis获取多层记忆
    soul = await PromptManager.get_soul("soul")
    core_anchor = redis_client.get(build_memory_key(
        LLMChatConstant.CORE_ANCHOR, user_id, llm_id
    ))
    character_card_json = redis_client.get(build_memory_key(
        LLMChatConstant.CHARACTER_CARD, user_id, llm_id
    ))
    character_card = json.loads(character_card_json) if character_card_json else {}
    
    # 构建提示词
    prompt = PromptTemplate.CHAT_SYSTEM_PROMPT_TEMPLATE.format(
        soul=soul,
        role_declaration=character_card.get("名字", "助手"),
        core_anchor=core_anchor,
        character_card=json.dumps(character_card, ensure_ascii=False),
        mes_example=character_card.get("示例对话", ""),
        relevant_memories="...",
        recent_chat="...",
        user_message="用户: 在吗?\n助手: "
    )
    
    return prompt
```

## 创建新的 Soul

创建新的 Soul 文件，只需包含三个部分：

```markdown
# Soul - 角色灵魂定义

## 角色身份
（1-2句话描述角色）

## 核心原则
1. 原则1
2. 原则2
3. 原则3

## 绝对边界
- 边界1
- 边界2
- 边界3
```

## Soul 文件命名规范

- 文件名格式：`{角色类型}_soul.md` 或直接 `soul.md`
- 示例：
  - `soul.md` - 默认通用角色
  - `girlfriend_soul.md` - 女朋友
  - `doctor_soul.md` - 医生
  - `friend_soul.md` - 朋友
  - `teacher_soul.md` - 老师

## 相关文件

- `soul.md` - 默认角色 soul 示例
- `prompt_template.py` - 提示词模板
- `prompt_manager.py` - 提示词管理（含Soul加载方法）
- `character_card.md` - 角色卡生成模板（包含动作表达规则）
- `role_memory_core.md` - 角色核心锚点生成模板
- `memory_upload_service.py` - 多层记忆架构服务

## 注意事项

1. **Soul 要保持简洁**：只定义核心，不包含实现细节
2. **角色卡是动态的**：具体动作、语言风格在角色卡中定义
3. **Soul 可以复用**：相似的角色可以共享同一个 Soul
4. **层次分明**：Soul（抽象） → 角色卡（具体） → 对话（实例）
