import asyncio
import os

from loguru import logger
from functools import lru_cache

DIR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "")


class PromptManager:
    @classmethod
    @lru_cache(maxsize=24)
    def _get_prompt_sync(cls, file_name: str) -> str | None:
        if not file_name.endswith((".txt", ".md")):
            file_name += ".md"

        file_path = os.path.join(DIR_PATH, file_name)

        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        else:
            logger.error(f"导入提示词文件错误：文件路径不存在:{file_path}")
            return None

    @classmethod
    async def get_prompt(cls, file_name: str) -> str | None:
        return cls._get_prompt_sync(file_name)
    
    @classmethod
    async def get_soul(cls, soul_name: str = "soul") -> str | None:
        """
        加载角色灵魂（Soul）提示词
        
        Args:
            soul_name: Soul文件名（不包含扩展名），默认 soul
            
        Returns:
            Soul提示词内容，如果文件不存在返回None
        """
        return await cls.get_prompt(soul_name)
    
    @classmethod
    @lru_cache(maxsize=8)
    def _get_available_souls_sync(cls) -> list[str]:
        """
        获取所有可用的soul文件列表
        
        Returns:
            可用的soul文件名列表（不含扩展名）
        """
        import re
        soul_files = []
        
        if os.path.exists(DIR_PATH):
            for file in os.listdir(DIR_PATH):
                if file.endswith('_soul.md'):
                    soul_name = file[:-9]
                    soul_files.append(soul_name)
        
        return soul_files
    
    @classmethod
    def get_available_souls(cls) -> list[str]:
        """
        获取所有可用的soul文件列表
        
        Returns:
            可用的soul文件名列表（不含扩展名）
        """
        return cls._get_available_souls_sync()
    
    @classmethod
    async def build_chat_prompt(
        cls,
        soul_name: str = "soul",
        role_declaration: str = "",
        core_anchor: str = "",
        character_card: str = "",
        mes_example: str = "",
        relevant_memories: str = "",
        recent_chat: str = "",
        user_message: str = ""
    ) -> str | None:
        """
        构建完整的聊天提示词，包含soul角色设定
        
        Args:
            soul_name: Soul文件名（不含扩展名）
            role_declaration: 角色声明
            core_anchor: 角色核心锚点
            character_card: 角色详细卡
            mes_example: 示例对话风格
            relevant_memories: 相关记忆
            recent_chat: 最近对话
            user_message: 用户消息
            
        Returns:
            完整的提示词字符串，如果soul不存在返回None
        """
        from app.core.prompts.prompt_template import PromptTemplate
        
        soul = await cls.get_soul(soul_name)
        if not soul:
            logger.error(f"Soul文件不存在: {soul_name}")
            return None
        
        prompt = PromptTemplate.CHAT_SYSTEM_PROMPT_TEMPLATE.format(
            soul=soul,
            role_declaration=role_declaration,
            core_anchor=core_anchor,
            character_card=character_card,
            mes_example=mes_example,
            relevant_memories=relevant_memories,
            recent_chat=recent_chat,
            user_message=user_message
        )
        
        return prompt