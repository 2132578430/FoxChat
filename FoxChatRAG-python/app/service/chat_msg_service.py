"""
对话消息服务模块

职责：
- 对话主入口（chat_msg）
- 处理用户消息，构建 Prompt
- 调用 LLM 生成回复
- 触发后台任务（记忆总结）
- 解析 LLM 返回（去除 think 标签，解析 action 标签）
- 清理对话记忆（delete_msg）
"""

import json
import re
from typing import List

from loguru import logger

from app.common.constant.ChromaTypeConstant import ChromaTypeConstant
from app.common.constant.LLMChatConstant import LLMChatConstant, build_memory_key
from app.common.constant.MsgStatusConstant import MsgStatusConstant
from app.core.db.redis_client import redis_client
from app.core.llm_model import model as llm_model
from app.core.prompts.prompt_manager import PromptManager
from app.core.prompts.prompt_template import PromptTemplate
from app.exception.BusinessException import BusinessException
from app.schemas import ChatMsgTo, MessageBlock
from app.service.chat.memory_summary_service import async_summary_msg
from app.util import chroma_util
from fastapi import BackgroundTasks
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableLambda
from langchain_core.language_models.chat_models import BaseMessage
from langchain_core.documents import Document


def strip_think_tags(content: str) -> str:
    """去除 LLM 返回的 <think> 标签及其内容"""
    if not content:
        return ""
    return re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()


def parse_action_tags(content: str) -> List[MessageBlock]:
    """解析 <action> 标签，将消息分割成多个消息块"""
    if not content:
        return []

    pattern = r'<action>(.*?)</action>'
    matches = list(re.finditer(pattern, content, re.DOTALL))

    if not matches:
        return [MessageBlock(type="text", text=content.strip())]

    blocks: List[MessageBlock] = []

    first_action_start = matches[0].start()
    if first_action_start > 0:
        before_text = content[:first_action_start].strip()
        if before_text:
            blocks.append(MessageBlock(type="text", text=before_text))

    for i, match in enumerate(matches):
        action_text = match.group(1).strip()
        match_end = match.end()

        if i < len(matches) - 1:
            next_match_start = matches[i + 1].start()
            after_text = content[match_end:next_match_start].strip()
        else:
            after_text = content[match_end:].strip()

        if after_text:
            blocks.append(MessageBlock(type="action_text", action=action_text, text=after_text))
        else:
            blocks.append(MessageBlock(type="action", action=action_text, text=None))

    return blocks


async def _documents_format(documents: List[Document]) -> str:
    """将文档列表格式化为 JSON 字符串"""
    format_str = "["
    for doc in documents:
        format_str = format_str + doc.page_content + ", "
    format_str += "]"
    return format_str


async def print_template(template_value):
    """打印注入的模板变量（调试用）"""
    logger.debug(f"注入消息：{template_value}")
    return template_value


async def _build_chat_chain():
    """构建对话 Chain"""
    template = ChatPromptTemplate(
        [
            ("system", PromptTemplate.CHAT_SYSTEM_PROMPT_TEMPLATE),
            MessagesPlaceholder("history_msg"),
            ("human", "Reply with a response that matches the current memory and identity according to the above prompts and memory template：\n{user_message}")
        ]
    )

    str_parser = StrOutputParser()
    llm = await llm_model.get_chat_model()
    chain = template | RunnableLambda(print_template) | llm | str_parser
    return chain


async def _build_history_message(recent_msg: List[str]) -> List[BaseMessage]:
    """将 Redis 中的消息列表转换为 LangChain 的历史消息格式"""
    if len(recent_msg) == 0:
        return []

    history_msg: List[BaseMessage] = []

    for msg_json in reversed(recent_msg):
        msg: dict = json.loads(msg_json)
        role = msg.get("role")
        content = msg.get("content")

        if role == "human":
            history_msg.append(HumanMessage(content))
        elif role == "ai":
            history_msg.append(AIMessage(content))
        else:
            raise BusinessException(MsgStatusConstant.UNKNOWN_ROLE_ERROR)

    return history_msg


async def chat_msg(msg: ChatMsgTo, background_tasks: BackgroundTasks) -> List[MessageBlock]:
    """
    对话主流程

    流程：
    1. 批量获取各种记忆（初始化记忆、角色卡、核心锚点、用户画像、Memory Bank）
    2. 格式化记忆为可读文本
    3. 获取对话历史
    4. 检索相关记忆
    5. 构建 Prompt，调用 LLM
    6. 保存对话到 Redis
    7. 触发后台任务（记忆总结）
    8. 解析 LLM 返回并返回
    """
    llm_id = msg.llmId
    msg_content = msg.msgContent
    user_id = msg.userId

    # 构建 Redis key
    init_memory_key = (LLMChatConstant.CHAT_MEMORY +
                       user_id + ":" +
                       llm_id + ":" +
                       LLMChatConstant.INIT_MEMORY)
    recent_msg_key = (LLMChatConstant.CHAT_MEMORY +
                      user_id + ":" +
                      llm_id + ":" +
                      LLMChatConstant.RECENT_MSG)

    pip = redis_client.pipeline()

    character_card_key = build_memory_key(LLMChatConstant.CHARACTER_CARD, user_id, llm_id)
    core_anchor_key = build_memory_key(LLMChatConstant.CORE_ANCHOR, user_id, llm_id)
    user_profile_key = build_memory_key(LLMChatConstant.USER_PROFILE, user_id, llm_id)
    memory_bank_key = build_memory_key(LLMChatConstant.MEMORY_BANK, user_id, llm_id)

    # 批量获取
    pip.get(init_memory_key)
    pip.lrange(recent_msg_key, 0, 29)
    pip.get(character_card_key)
    pip.get(core_anchor_key)
    pip.get(user_profile_key)
    pip.get(memory_bank_key)

    result = pip.execute()

    # 提取记忆
    init_memory: str = result[0]
    recent_msg: List[str] = result[1]
    character_card_json: str = result[2]
    core_anchor_json: str = result[3]
    user_profile_json: str = result[4]
    memory_bank_json: str = result[5]

    # 解析角色卡
    character_card_examples = ""
    character_card_detail = ""
    if character_card_json:
        try:
            character_card = json.loads(character_card_json)
            character_card_examples = character_card.get("示例对话", "")

            parts = []
            if character_card.get("爱称"):
                parts.append(f"爱称：{', '.join(character_card['爱称'])}")
            if character_card.get("性格关键词"):
                parts.append(f"性格关键词：{character_card['性格关键词']}")
            if character_card.get("动作风格"):
                parts.append(f"动作风格：{character_card['动作风格']}")
            if character_card.get("常用动作"):
                parts.append(f"常用动作：{', '.join(character_card['常用动作'])}")
            if character_card.get("核心描述"):
                parts.append(f"核心描述：{character_card['核心描述']}")
            if parts:
                character_card_detail = "\n".join(parts)
        except json.JSONDecodeError:
            logger.warning(f"角色卡JSON解析失败")
            character_card_examples = ""
            character_card_detail = ""

    # 解析核心锚点
    role_declaration = ""
    core_anchor_text = ""
    if core_anchor_json:
        role_declaration_match = re.search(r'【角色声明】\s*(.+?)(?=【角色核心锚点】|$)', core_anchor_json, re.DOTALL)
        if role_declaration_match:
            role_declaration = role_declaration_match.group(1).strip()

        core_anchor_match = re.search(r'【角色核心锚点】\s*(.+?)(?=【绝对边界】|$)', core_anchor_json, re.DOTALL)
        if core_anchor_match:
            core_anchor_text = core_anchor_match.group(1).strip()

    # 解析用户画像
    user_profile_summary = ""
    if user_profile_json:
        try:
            up = json.loads(user_profile_json)
            parts = []
            for dim_key, dim_val in up.items():
                if isinstance(dim_val, dict):
                    items = [f"{k}：{v}" for k, v in dim_val.items() if v and v != "[未提及]"]
                    if items:
                        parts.append(f"{dim_key}：{', '.join(items)}")
                elif isinstance(dim_val, list):
                    filtered = [v for v in dim_val if v and v != "[未提及]"]
                    if filtered:
                        parts.append(f"{dim_key}：{', '.join(filtered)}")
                elif dim_val and dim_val != "[未提及]":
                    parts.append(f"{dim_key}：{dim_val}")
            if parts:
                user_profile_summary = "\n".join(parts)
        except json.JSONDecodeError:
            logger.warning(f"user_profile JSON解析失败")

    # 解析 Memory Bank
    memory_bank_summary = ""
    if memory_bank_json:
        try:
            mb = json.loads(memory_bank_json)
            if isinstance(mb, list) and mb:
                lines = []
                for item in mb:
                    time_val = item.get("time", "某时")
                    content_val = item.get("content", "")
                    type_val = item.get("type", "event")
                    lines.append(f"- [{time_val}]（{type_val}）{content_val}")
                memory_bank_summary = "\n".join(lines)
        except json.JSONDecodeError:
            logger.warning(f"memory_bank JSON解析失败")

    # 构建历史消息
    history_msg: List[BaseMessage] = await _build_history_message(recent_msg)

    # 检索相关记忆
    documents = await chroma_util.search(
        ChromaTypeConstant.CHAT,
        msg_content,
        {"user_id": user_id, "llm_id": llm_id})
    total_memory = await _documents_format(documents)

    # 调用 LLM
    chain = await _build_chat_chain()
    soul = await PromptManager.get_soul("soul")

    logger.debug("api调用llm最后提示")
    chat_response = await chain.ainvoke({
        "soul": soul if soul else "",
        "role_declaration": role_declaration,
        "core_anchor": core_anchor_text,
        "character_card": init_memory if init_memory else "",
        "mes_example": character_card_examples,
        "character_card_detail": character_card_detail if character_card_detail else "",
        "user_profile_summary": user_profile_summary if user_profile_summary else "",
        "memory_bank_summary": memory_bank_summary if memory_bank_summary else "",
        "relevant_memories": total_memory,
        "recent_chat": "",
        "history_msg": history_msg,
        "user_message": msg_content,
    })

    # 保存对话到 Redis
    pip = redis_client.pipeline()
    pip.lpush(recent_msg_key, json.dumps({"role": "human", "content": msg_content}))
    pip.lpush(recent_msg_key, json.dumps({"role": "ai", "content": chat_response}))
    pip.llen(recent_msg_key)

    result = pip.execute()

    # 触发后台任务：记忆总结（每6轮触发一次）
    background_tasks.add_task(async_summary_msg, recent_msg_key, result[0], user_id, llm_id)

    # 解析返回
    clean_response = strip_think_tags(chat_response)
    message_blocks = parse_action_tags(clean_response)

    return message_blocks


async def delete_msg(user_id: str, llm_id: str) -> None:
    """删除对话相关的所有记忆"""
    keys_to_delete = [
        build_memory_key(LLMChatConstant.RAW_EXPERIENCE, user_id, llm_id),
        build_memory_key(LLMChatConstant.CORE_ANCHOR, user_id, llm_id),
        build_memory_key(LLMChatConstant.USER_PROFILE, user_id, llm_id),
        build_memory_key(LLMChatConstant.CHARACTER_CARD, user_id, llm_id),
        build_memory_key(LLMChatConstant.MEMORY_BANK, user_id, llm_id),
        build_memory_key(LLMChatConstant.INIT_MEMORY, user_id, llm_id),
        build_memory_key(LLMChatConstant.RECENT_MSG, user_id, llm_id),
    ]

    pip = redis_client.pipeline()
    for key in keys_to_delete:
        pip.delete(key)
    pip.execute()

    await chroma_util.delete(ChromaTypeConstant.CHAT, user_id=user_id, llm_id=llm_id)
