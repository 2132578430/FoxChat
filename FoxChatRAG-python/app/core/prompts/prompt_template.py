from enum import StrEnum


class PromptTemplate(StrEnum):
    CHAT_SYSTEM_PROMPT_TEMPLATE = """
    【静态锚点】
    {static_anchors}

    【用户画像】
    {user_profile_summary}

    【历史上下文】
    {historical_context}

    【当前状态】
    {current_state}
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