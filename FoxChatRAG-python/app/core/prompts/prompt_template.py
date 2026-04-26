from enum import StrEnum


class PromptTemplate(StrEnum):
    CHAT_SYSTEM_PROMPT_TEMPLATE = """
    【角色灵魂（Soul）】
    {soul}
    
    【角色声明】
    {role_declaration}
    
    【角色核心锚点】
    {core_anchor}
    
    【角色详细卡】
    {character_card}

    【角色特征补充】
    {character_card_detail}

    【称呼约定】
    {call_convention}

    【用户画像】
    {user_profile_summary}

    【记忆银行】
    {memory_bank_summary}
    
    【示例对话风格】
    {mes_example}
    
    【相关记忆】
    {relevant_memories}
    
    【最近对话】
    {recent_chat}
    
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
