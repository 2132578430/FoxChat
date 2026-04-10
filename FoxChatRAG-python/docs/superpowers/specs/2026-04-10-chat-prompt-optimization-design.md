# 聊天提示词模板优化设计

## 概述

优化 FoxChatRAG 项目的聊天提示词模板，将 CORE_ANCHOR（角色核心锚点）集成到聊天流程中，移除 soul.md 依赖，提升角色一致性。

## 背景

当前问题：
- `CORE_ANCHOR` 已生成并存储，但未在聊天提示词模板中使用
- `soul.md` 与模板变量名不匹配，造成代码混乱
- 提示词模板结构简单，有优化空间

## 目标

1. 将 CORE_ANCHOR 添加到聊天提示词模板
2. 移除 soul.md 依赖
3. 修复模板变量名与服务代码的不匹配
4. 优化提示词结构，参考开源最佳实践

## 改动详情

### 1. 删除 soul.md

删除文件：`app/core/prompts/soul.md`

### 2. 更新 prompt_template.py

**新 CHAT_SYSTEM_PROMPT_TEMPLATE：**

```
【角色声明】
{role_declaration}

【角色核心锚点】
{core_anchor}

【角色详细卡】
{character_card}

【示例对话风格】
{mes_example}

【相关记忆】
{relevant_memories}

【最近对话】
{recent_chat}

【行为准则】
- 回复简洁自然，像真实聊天
- 不要替用户做决定
- 直接回应用户消息
- 不要过度关心或重复话题

{user_message}
```

### 3. 更新 chat_msg_service.py

**获取数据：**
- 从 Redis 获取 `CORE_ANCHOR`
- 从 Redis 获取 `CHARACTER_CARD`

**修复变量映射：**

| 模板变量 | 代码变量 | 说明 |
|---------|---------|------|
| `{role_declaration}` | `role_declaration` | 从 CORE_ANCHOR 提取 |
| `{core_anchor}` | `core_anchor` | 角色核心锚点 |
| `{character_card}` | `character_card` | 角色详细描述 |
| `{mes_example}` | `mes_example` | 示例对话 |
| `{relevant_memories}` | `long_term_memory` | 来自 Chroma |
| `{recent_chat}` | - | 通过 MessagesPlaceholder 注入 |
| `{user_message}` | `chat_msg` | 用户消息 |

**移除逻辑：**
- 删除 `PromptManager.get_prompt("soul.md")`
- 删除 `soul` 变量注入

### 4. 变量提取逻辑

从 `CORE_ANCHOR` 提取 `role_declaration`：
- 格式：`【角色声明】` 部分的第一行
- 示例：`[我]是活泼可爱的狐狸助手，[对方]是用户`

## 实施步骤

1. 更新 `prompt_template.py` - 修改模板结构
2. 更新 `chat_msg_service.py` - 修复变量映射，移除 soul
3. 删除 `soul.md` 文件
4. 测试验证聊天功能

## 预期效果

- 角色一致性增强（CORE_ANCHOR 提供绝对边界）
- Token 消耗减少（移除 soul.md）
- 代码更清晰（变量名匹配）
- 保持中国用户习惯的聊天风格
