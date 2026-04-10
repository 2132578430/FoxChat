from enum import StrEnum


class PromptTemplate(StrEnum):
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

    SUMMARY_SYSTEM_PROMPT_TEMPLATE = """
    分析对话，提取需要记住的信息。
    
    要求：
    - 只记录重要事实和新信息
    - 不记录闲聊和问候
    - 输出一段话
    
    对话：
    {recent_msg_list}
    """

    MEMORY_EVENT_EXTRACTOR_PROMPT = """
从对话历史中提取关键事件和状态变化。

要求：
- 只提取重要的、新的话题或事件
- 忽略日常闲聊和问候
- 每条事件包含：time（当前日期）、type（event 或 state）、content（事件描述）
- type 字段说明：event=具体事件，state=状态或关系变化
- 输出 JSON 数组格式
- 只输出 JSON 数组，不要其他文字

对话历史：
{chat_history}

输出：
"""
