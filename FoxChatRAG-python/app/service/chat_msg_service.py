import json
from datetime import datetime
from typing import List

from fastapi import BackgroundTasks, Request
from langchain_core.documents import Document
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableLambda
from loguru import logger

from app.common import ChromaTypeConstant, FileTypeConstant
from app.common.constant.LLMChatConstant import LLMChatConstant
from app.core import llm_model
from app.core.db.redis_client import redis_client
from app.core.net import ip_client
from app.core.prompts.prompt_manager import PromptManager
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
            ("human", "Reply with a response that matches the current memory and identity according to the above prompts and memory template：{chat_msg}")
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

    summary_msg = await chain.ainvoke({"chat_msg": recent_msg_list})

    # 分割摘要
    documents = await loader_util.load_file(summary_msg, FileTypeConstant.STR)

    source_id = user_id + llm_id + summary_msg

    # 存入chroma
    await chroma_util.upload(ChromaTypeConstant.CHAT, documents, source_id, user_id=user_id, llm_id=llm_id)

async def _get_dynamic_content(request: Request) -> dict:
    dynamic = {
        "current_time": datetime.now(),
        "location": "未知",
        "temperature": "未知",
        "windspeed": "未知",
    }

    real_ip = await ip_client.get_read_ip(request)
    if real_ip in ("127.0.0.1", "localhost", "0.0.0.0"):
        real_ip = None

    local:dict = await ip_client.get_current_location(real_ip)
    dynamic["location"] = local.get("location")

    lat = local.get("lat")
    lon = local.get("lon")

    weather:dict = await ip_client.get_weather(lat, lon)
    dynamic["weather"] = weather.get("temperature")
    dynamic["windspeed"] = weather.get("windspeed")

    return dynamic

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

    pip.get(init_memory_key)
    pip.lrange(recent_msg_key, 0, 29)

    # 运行redis
    result = pip.execute()

    init_memory: str = result[0]
    recent_msg: List[str] = result[1]

    # 将聊天记忆转化为历史消息
    history_msg: List[BaseMessage] = await _build_history_message(recent_msg)

    # 获取chroma中记忆片段
    documents = await chroma_util.search(
        ChromaTypeConstant.CHAT,
        msg_content,
        {"user_id": user_id, "llm_id": llm_id})
    total_memory = await _documents_format(documents)

    # 取出soul提示词
    soul = await PromptManager.get_prompt("soul.md")

    # 获取当前动态信息
    dynamic_content = await _get_dynamic_content(request)

    logger.debug(f"当前动态信息：{dynamic_content}")

    chain = await _build_chat_chain()

    logger.debug("api调用llm最后提示")
    chat_response = await chain.ainvoke({
        "soul": soul,
        "init_memory": init_memory,
        "long_term_memory": total_memory,
        "history_msg": history_msg,
        "dynamic_context": dynamic_content,
        "chat_msg": msg_content,
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
    init_memory_key = (LLMChatConstant.CHAT_MEMORY +
                       user_id + ":" +
                       llm_id + ":" +
                       LLMChatConstant.INIT_MEMORY)
    recent_msg_key = (LLMChatConstant.CHAT_MEMORY +
                      user_id + ":" +
                      llm_id + ":" +
                      LLMChatConstant.RECENT_MSG)
    # 先删除redis
    pip = redis_client.pipeline()

    pip.delete(init_memory_key)
    pip.delete(recent_msg_key)

    pip.execute()

    # 再删除chroma
    await chroma_util.delete(ChromaTypeConstant.CHAT, user_id=user_id, llm_id=llm_id)