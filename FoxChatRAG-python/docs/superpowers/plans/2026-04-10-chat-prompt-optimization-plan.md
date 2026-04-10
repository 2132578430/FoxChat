# 聊天提示词模板优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 CORE_ANCHOR 集成到聊天提示词，移除 soul.md 依赖，修复变量映射

**Architecture:** 修改 prompt_template.py 模板结构，更新 chat_msg_service.py 变量映射，删除 soul.md 文件

**Tech Stack:** Python, FastAPI, LangChain, Redis

---

## Task 1: 更新 prompt_template.py

**Files:**
- Modify: `app/core/prompts/prompt_template.py`

- [ ] **Step 1: 替换 CHAT_SYSTEM_PROMPT_TEMPLATE**

打开 `app/core/prompts/prompt_template.py`，将 `CHAT_SYSTEM_PROMPT_TEMPLATE` 替换为：

```python
CHAT_SYSTEM_PROMPT_TEMPLATE = """
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
"""
```

- [ ] **Step 2: 提交更改**

```bash
git add app/core/prompts/prompt_template.py
git commit -m "feat: update chat prompt template with CORE_ANCHOR"
```

---

## Task 2: 更新 chat_msg_service.py

**Files:**
- Modify: `app/service/chat_msg_service.py`

- [ ] **Step 1: 添加 CORE_ANCHOR 获取逻辑**

在 `chat_msg_service.py` 的第 163 行附近，找到：
```python
character_card_key = build_memory_key(LLMChatConstant.CHARACTER_CARD, user_id, llm_id)
```

在其后添加：
```python
core_anchor_key = build_memory_key(LLMChatConstant.CORE_ANCHOR, user_id, llm_id)
```

- [ ] **Step 2: 更新 Redis pipeline 获取**

找到：
```python
pip.get(character_card_key)
```

替换为：
```python
pip.get(character_card_key)
pip.get(core_anchor_key)
```

- [ ] **Step 3: 添加变量解析**

找到：
```python
character_card_json: str = result[2]
```

在其后添加：
```python
core_anchor_json: str = result[3]
```

- [ ] **Step 4: 解析 CORE_ANCHOR 提取 role_declaration**

找到解析 character_card 的代码块（约第 176-184 行），在其后添加：

```python
role_declaration = ""
if core_anchor_json:
    try:
        core_anchor_obj = json.loads(core_anchor_json)
        role_declaration = core_anchor_obj.get("role_declaration", "")
        core_anchor_text = core_anchor_obj.get("core_anchor", "")
    except json.JSONDecodeError:
        logger.warning(f"CORE_ANCHOR JSON解析失败: {core_anchor_json}")
        core_anchor_text = ""
else:
    core_anchor_text = ""
```

- [ ] **Step 5: 移除 soul 相关逻辑**

找到（约第 196-198 行）：
```python
soul = await PromptManager.get_prompt("soul.md")
```

删除这行。

- [ ] **Step 6: 修复 chain.ainvoke 参数**

找到（约第 207-215 行）：
```python
chat_response = await chain.ainvoke({
    "soul": soul,
    "init_memory": init_memory,
    "long_term_memory": total_memory,
    "character_card_examples": character_card_examples,
    "history_msg": history_msg,
    "dynamic_context": dynamic_content,
    "chat_msg": msg_content,
})
```

替换为：
```python
chat_response = await chain.ainvoke({
    "role_declaration": role_declaration,
    "core_anchor": core_anchor_text,
    "character_card": init_memory if init_memory else "",
    "mes_example": character_card_examples,
    "relevant_memories": total_memory,
    "recent_chat": "",
    "history_msg": history_msg,
    "user_message": msg_content,
})
```

- [ ] **Step 7: 提交更改**

```bash
git add app/service/chat_msg_service.py
git commit -m "feat: integrate CORE_ANCHOR into chat flow, remove soul.md dependency"
```

---

## Task 3: 删除 soul.md

**Files:**
- Delete: `app/core/prompts/soul.md`

- [ ] **Step 1: 删除文件**

```bash
rm app/core/prompts/soul.md
```

- [ ] **Step 2: 提交更改**

```bash
git add -A
git commit -m "feat: remove soul.md, replaced by CORE_ANCHOR"
```

---

## 验证

- [ ] 检查所有变量映射是否正确
- [ ] 确认 soul.md 已删除
- [ ] 测试聊天功能是否正常

---

**Plan complete.**