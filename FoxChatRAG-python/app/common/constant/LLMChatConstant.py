from enum import StrEnum


class LLMChatConstant(StrEnum):
    CHAT_MEMORY = "chat:memory:"
    INIT_MEMORY = "role_init_memory"
    RECENT_MSG = "recent_msg"