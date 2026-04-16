"""
记忆总结服务模块

职责：
- 6轮对话后的消息总结（存入向量数据库）
- 从对话中提取关键事件（存入 Memory Bank）
- Memory Bank 压缩（超过阈值时触发）
- 用户画像更新（在总结流程中调用）
"""

import json
from datetime import datetime
from typing import List

from loguru import logger

from app.common.constant.ChromaTypeConstant import ChromaTypeConstant
from app.common.constant.FileTypeConstant import FileTypeConstant
from app.common.constant.LLMChatConstant import LLMChatConstant, build_memory_key
from app.core.db.redis_client import redis_client
from app.core.llm_model import model as llm_model
from app.core.prompts.prompt_template import PromptTemplate
from app.util import loader_util, chroma_util
from app.service.chat.user_profile_service import update_user_profile_in_summary

MEMORY_BANK_MAX_SIZE = 50
MEMORY_BANK_COMPRESS_TARGET = 30


async def _build_summary_chain():
    """构建消息总结 Chain"""
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    template = ChatPromptTemplate(
        [
            ("system", PromptTemplate.SUMMARY_SYSTEM_PROMPT_TEMPLATE),
            ("human", "The chat history between the user and the role currently played by the AI is: {chat_history_msg}")
        ]
    )

    str_parser = StrOutputParser()
    llm = await llm_model.get_summary_model()
    chain = template | llm | str_parser
    return chain


async def _build_event_extractor_chain():
    """构建事件提取 Chain"""
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    template = ChatPromptTemplate([
        ("system", PromptTemplate.MEMORY_EVENT_EXTRACTOR_PROMPT),
        ("human", "{chat_history}")
    ])

    str_parser = StrOutputParser()
    llm = await llm_model.get_extraction_model()
    chain = template | llm | str_parser
    return chain


async def _extract_memory_events(recent_msg_list: List[str]) -> List[dict]:
    """从对话历史中提取关键事件"""
    if not recent_msg_list:
        return []

    chat_history = "\n".join(recent_msg_list)
    chain = await _build_event_extractor_chain()
    result = await chain.ainvoke({"chat_history": chat_history})

    try:
        events = json.loads(result)
        current_time = datetime.now().strftime("%Y-%m-%d")
        for event in events:
            if "time" not in event or not event["time"]:
                event["time"] = current_time
        return events
    except json.JSONDecodeError:
        logger.warning(f"事件提取 JSON 解析失败: {result}")
        return []


async def _append_to_memory_bank(events: List[dict], user_id: str, llm_id: str) -> None:
    """将提取的事件追加到 Memory Bank"""
    if not events:
        return

    memory_bank_key = build_memory_key(LLMChatConstant.MEMORY_BANK, user_id, llm_id)

    existing = redis_client.get(memory_bank_key)
    if existing:
        try:
            memory_bank = json.loads(existing)
        except json.JSONDecodeError:
            memory_bank = []
    else:
        memory_bank = []

    memory_bank.extend(events)
    redis_client.set(memory_bank_key, json.dumps(memory_bank, ensure_ascii=False))
    logger.debug(f"已追加 {len(events)} 条事件到 memory_bank")


async def _compress_memory_bank_if_needed(user_id: str, llm_id: str) -> None:
    """检查并压缩 Memory Bank（超过阈值时）"""
    memory_bank_key = build_memory_key(LLMChatConstant.MEMORY_BANK, user_id, llm_id)

    existing = redis_client.get(memory_bank_key)
    if not existing:
        return

    try:
        memory_bank = json.loads(existing)
    except json.JSONDecodeError:
        return

    if len(memory_bank) < MEMORY_BANK_MAX_SIZE:
        return

    logger.info(f"memory_bank 长度 {len(memory_bank)} 超过阈值，开始压缩...")

    compress_prompt = f"""将以下记忆库压缩到 {MEMORY_BANK_COMPRESS_TARGET} 条核心事件。
    要求：
    - 合并相似事件
    - 保留最重要的关键事件
    - 保持 time、type、content 字段
    - 输出 JSON 数组格式
    - 只输出 JSON 数组，不要其他文字

    当前记忆库：
    {json.dumps(memory_bank, ensure_ascii=False, indent=2)}

    压缩后的记忆库：
    """

    llm = await llm_model.get_memory_model()
    compressed = await llm.ainvoke(compress_prompt)

    try:
        compressed_memory_bank = json.loads(compressed)
        redis_client.set(memory_bank_key, json.dumps(compressed_memory_bank, ensure_ascii=False))
        logger.info(f"memory_bank 压缩完成: {len(memory_bank)} -> {len(compressed_memory_bank)} 条")
    except json.JSONDecodeError:
        logger.warning(f"memory_bank 压缩 JSON 解析失败")


async def _summary_and_upload(recent_msg_list: List[str], user_id: str, llm_id: str) -> None:
    """总结消息并上传到向量数据库"""
    chain = await _build_summary_chain()
    summary_msg = await chain.ainvoke({
        "chat_history_msg": recent_msg_list,
        "recent_msg_list": recent_msg_list
    })

    documents = loader_util.load_file(summary_msg, FileTypeConstant.STR)
    source_id = user_id + llm_id + summary_msg
    await chroma_util.upload(ChromaTypeConstant.CHAT, documents, source_id, user_id=user_id, llm_id=llm_id)


async def async_summary_msg(recent_msg_key: str, recent_msg_size: int, user_id: str, llm_id: str) -> None:
    """
    异步消息总结主流程（每6轮触发一次）

    流程：
    1. 取出最近消息，保留最近10条
    2. 总结消息存入向量数据库
    3. 提取关键事件存入 Memory Bank
    4. 检查并压缩 Memory Bank（如需要）
    5. 更新用户画像
    """
    if recent_msg_size < 12:
        return

    pip = redis_client.pipeline()
    pip.lrange(recent_msg_key, 9, -1)
    pip.ltrim(recent_msg_key, 0, 9)

    result = pip.execute()
    recent_msg_list: list[str] = result[0]
    recent_msg_list.reverse()

    await _summary_and_upload(recent_msg_list, user_id, llm_id)

    events = await _extract_memory_events(recent_msg_list)
    if events:
        await _append_to_memory_bank(events, user_id, llm_id)

    await _compress_memory_bank_if_needed(user_id, llm_id)

    await update_user_profile_in_summary(user_id, llm_id, recent_msg_list)
