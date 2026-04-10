import json
import re
from typing import List

from fastapi import BackgroundTasks, Request
from langchain_core.documents import Document
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableLambda
from loguru import logger

from app.common import ChromaTypeConstant, FileTypeConstant
from app.common.constant.LLMChatConstant import LLMChatConstant, build_memory_key
from app.core import llm_model
from app.core.db.redis_client import redis_client
from app.core.prompts.prompt_template import PromptTemplate
from app.schemas import ChatMsgTo
from app.util import loader_util, chroma_util


async def _documents_format(documents: List[Document]) -> str:
    format_str = "["

    for doc in documents:
        format_str = format_str + doc.page_content + ", "

    format_str += "]"
    return format_str

async def print_template(template_value):
    logger.debug(f"注入消息：{template_value}")
    return template_value

async def _build_chat_chain():
    template = ChatPromptTemplate(
        [
            ("system", PromptTemplate.CHAT_SYSTEM_PROMPT_TEMPLATE),
            MessagesPlaceholder("history_msg"),
            ("human", "Reply with a response that matches the current memory and identity according to the above prompts and memory template：{user_message}")
        ]
    )

    str_parser = StrOutputParser()
    llm = await llm_model.get_llm_model("ds_model")

    chain = template | RunnableLambda(print_template) | llm | str_parser

    return chain

async def _build_history_message(recent_msg: List[str]) -> List[BaseMessage]:
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
            raise Exception(f"Unknown role: {role}")

    return history_msg

async def _build_summary_chain():
    template = ChatPromptTemplate(
        [
            ("system", PromptTemplate.SUMMARY_SYSTEM_PROMPT_TEMPLATE),
            ("human", "The chat history between the user and the role currently played by the AI is: {chat_history_msg}")
        ]
    )

    str_parser = StrOutputParser()
    llm = await llm_model.get_llm_model("qwen4b_model")

    chain = template | llm | str_parser

    return chain

async def _build_event_extractor_chain():
    """
    构建事件提取 chain，用于从对话中提取关键事件
    """
    template = ChatPromptTemplate([
        ("system", PromptTemplate.MEMORY_EVENT_EXTRACTOR_PROMPT),
        ("human", "{chat_history}")
    ])

    str_parser = StrOutputParser()
    llm = await llm_model.get_llm_model("qwen4b_model")

    chain = template | llm | str_parser

    return chain

async def _async_summary_msg(recent_msg_key: str, recent_msg_size: int, user_id: str, llm_id: str) -> None:
    if recent_msg_size < 30:
        return

    # 当大于等于30条记录，取出消息
    pip = redis_client.pipeline()

    pip.lrange(recent_msg_key, 9, -1)
    pip.ltrim(recent_msg_key, 0, 9)

    result = pip.execute()

    # result[0] 是 lrange 的结果 (list)，result[1] 是 ltrim 的结果 (bool)
    recent_msg_list: list[str] = result[0]
    recent_msg_list.reverse()

    # 总结消息
    chain = await _build_summary_chain()

    summary_msg = await chain.ainvoke({"chat_history_msg": recent_msg_list})

    # 分割摘要
    documents = await loader_util.load_file(summary_msg, FileTypeConstant.STR)

    source_id = user_id + llm_id + summary_msg

    # 存入chroma
    await chroma_util.upload(ChromaTypeConstant.CHAT, documents, source_id, user_id=user_id, llm_id=llm_id)

async def chat_msg(msg: ChatMsgTo, background_tasks: BackgroundTasks, request: Request) -> str:
    llm_id = msg.llmId
    msg_content = msg.msgContent
    user_id = msg.userId
    # 先去redis中取出来初始记忆
    init_memory_key = (LLMChatConstant.CHAT_MEMORY +
                       user_id + ":" +
                       llm_id + ":" +
                       LLMChatConstant.INIT_MEMORY)
    logger.debug(f"redis获取key:{init_memory_key}")

    # 从redis获取最近5次对话
    recent_msg_key = (LLMChatConstant.CHAT_MEMORY +
                      user_id + ":" +
                      llm_id + ":" +
                      LLMChatConstant.RECENT_MSG)

    # 开启pip通道
    pip = redis_client.pipeline()

    # 获取角色卡key
    character_card_key = build_memory_key(LLMChatConstant.CHARACTER_CARD, user_id, llm_id)
    core_anchor_key = build_memory_key(LLMChatConstant.CORE_ANCHOR, user_id, llm_id)

    pip.get(init_memory_key)
    pip.lrange(recent_msg_key, 0, 29)
    pip.get(character_card_key)
    pip.get(core_anchor_key)

    # 运行redis
    result = pip.execute()

    init_memory: str = result[0]
    recent_msg: List[str] = result[1]
    character_card_json: str = result[2]
    core_anchor_json: str = result[3]

    character_card_examples = ""
    if character_card_json:
        try:
            character_card = json.loads(character_card_json)
            character_card_examples = character_card.get("示例对话", "")
        except json.JSONDecodeError:
            logger.warning(f"角色卡JSON解析失败: {character_card_json}")
            character_card_examples = ""

    role_declaration = ""
    core_anchor_text = ""
    if core_anchor_json:
        role_declaration_match = re.search(r'【角色声明】\s*(.+?)(?=【角色核心锚点】|$)', core_anchor_json, re.DOTALL)
        if role_declaration_match:
            role_declaration = role_declaration_match.group(1).strip()
        
        core_anchor_match = re.search(r'【角色核心锚点】\s*(.+?)(?=【绝对边界】|$)', core_anchor_json, re.DOTALL)
        if core_anchor_match:
            core_anchor_text = core_anchor_match.group(1).strip()

    # 将聊天记忆转化为历史消息
    history_msg: List[BaseMessage] = await _build_history_message(recent_msg)

    # 获取chroma中记忆片段
    documents = await chroma_util.search(
        ChromaTypeConstant.CHAT,
        msg_content,
        {"user_id": user_id, "llm_id": llm_id})
    total_memory = await _documents_format(documents)

    chain = await _build_chat_chain()

    logger.debug("api调用llm最后提示")
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
    # 消息回复存入redis
    pip = redis_client.pipeline()
    pip.lpush(
        recent_msg_key,
        json.dumps({"role": "human", "content": msg_content}),
    )

    pip.lpush(
        recent_msg_key,
        json.dumps({"role": "ai", "content": chat_response}),
    )

    pip.llen(recent_msg_key)

    result = pip.execute()

    # 开启任务线程，总结消息
    background_tasks.add_task(_async_summary_msg, recent_msg_key, result[0], user_id, llm_id)

    return chat_response

async def delete_msg(user_id: str, llm_id: str) -> None:
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