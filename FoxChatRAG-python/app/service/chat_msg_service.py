import asyncio
import json
import re
from typing import Any, Dict, List, Optional, Literal

from fastapi import BackgroundTasks, Request
from langchain_core.documents import Document
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableLambda
from loguru import logger

from app.common import ChromaTypeConstant, FileTypeConstant
from app.common.constant.LLMChatConstant import LLMChatConstant, build_memory_key
from app.common.constant.MsgStatusConstant import MsgStatusConstant
from app.core import llm_model
from app.core.db.redis_client import redis_client
from app.core.prompts.prompt_manager import PromptManager
from app.core.prompts.prompt_template import PromptTemplate
from app.exception.BusinessException import BusinessException
from app.schemas import ChatMsgTo, MessageBlock
from app.util import loader_util, chroma_util, escape_template

# 记忆库压缩配置
MEMORY_BANK_MAX_SIZE = 50
MEMORY_BANK_COMPRESS_TARGET = 25


# =============== 消息块解析相关 ===============

from app.schemas import MessageBlock


def strip_think_tags(content: str) -> str:
    """
    去除消息中的 <think>...</think> 标签及其内容

    Args:
        content: LLM 返回的原始消息内容

    Returns:
        去除 think 标签后的纯文本
    """
    if not content:
        return ""
    # 匹配 <think>...</think> 标签及其中内容（贪婪模式整个匹配）
    return re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()


def parse_action_tags(content: str) -> List[MessageBlock]:
    """
    解析消息中的 <action> 标签，将消息分割成多个消息块

    解析规则：
    - 文本1<action>标签</action>文本2 -> text: {text: 文本1}, action_text: {action: 标签, text: 文本2}
    - <action>标签</action>文本2 -> action_text: {action: 标签, text: 文本2}
    - 文本1<action>标签</action> -> text: {text: 文本1}, action_only: {action: 标签}
    - <action>标签1</action><action>标签2</action> -> action_only: {action: 标签1}, action_only: {action: 标签2}
    - 纯文本 -> text: {text: 文本}

    Args:
        content: LLM 返回的原始消息内容

    Returns:
        消息块列表，每个块包含 type、action、text
    """
    if not content:
        return []

    # 正则匹配所有 <action>...</action> 标签及其位置
    pattern = r'<action>(.*?)</action>'
    matches = list(re.finditer(pattern, content, re.DOTALL))

    if not matches:
        # 没有 action 标签，返回普通文本块
        return [MessageBlock(type="text", text=content.strip())]

    blocks: List[MessageBlock] = []

    # 处理第一个 action 之前的文本
    first_action_start = matches[0].start()
    if first_action_start > 0:
        before_text = content[:first_action_start].strip()
        if before_text:
            blocks.append(MessageBlock(type="text", text=before_text))

    for i, match in enumerate(matches):
        action_text = match.group(1).strip()
        match_end = match.end()

        # 获取 action 标签之后的文本（直到下一个 action 或字符串末尾）
        if i < len(matches) - 1:
            next_match_start = matches[i + 1].start()
            after_text = content[match_end:next_match_start].strip()
        else:
            after_text = content[match_end:].strip()

        if after_text:
            # action 之后有文本 -> action_text 类型
            blocks.append(MessageBlock(type="action_text", action=action_text, text=after_text))
        else:
            # action 之后没有文本 -> action_only 类型
            blocks.append(MessageBlock(type="action", action=action_text, text=None))

    return blocks

# Chain 对象缓存（避免重复创建）
_CHAIN_CACHE: Dict[str, Any] = {}


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
    llm = await llm_model.get_chat_model()

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
            raise BusinessException(MsgStatusConstant.UNKNOWN_ROLE_ERROR)

    return history_msg

async def _build_summary_chain():
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
    """
    构建事件提取 chain，用于从对话中提取关键事件
    """
    template = ChatPromptTemplate([
        ("system", PromptTemplate.MEMORY_EVENT_EXTRACTOR_PROMPT),
        ("human", "{chat_history}")
    ])

    str_parser = StrOutputParser()
    llm = await llm_model.get_extraction_model()

    chain = template | llm | str_parser

    return chain

async def _extract_memory_events(recent_msg_list: List[str]) -> List[dict]:
    """
    从对话历史中提取关键事件
    """
    if not recent_msg_list:
        return []

    # 构建对话历史文本
    chat_history = "\n".join(recent_msg_list)

    # 调用 LLM 提取事件
    chain = await _build_event_extractor_chain()
    result = await chain.ainvoke({"chat_history": chat_history})

    # 解析 JSON 字符串
    try:
        events = json.loads(result)
        # 添加当前时间戳
        from datetime import datetime
        current_time = datetime.now().strftime("%Y-%m-%d")
        for event in events:
            if "time" not in event or not event["time"]:
                event["time"] = current_time
        return events
    except json.JSONDecodeError:
        logger.warning(f"事件提取 JSON 解析失败: {result}")
        return []

async def _append_to_memory_bank(events: List[dict], user_id: str, llm_id: str) -> None:
    """
    将提取的事件追加到 memory_bank

    Args:
        events: 待追加的事件列表
        user_id: 用户 ID
        llm_id: LLM ID
    """
    if not events:
        return

    memory_bank_key = build_memory_key(LLMChatConstant.MEMORY_BANK, user_id, llm_id)

    # 获取现有 memory_bank
    existing = redis_client.get(memory_bank_key)
    if existing:
        try:
            memory_bank = json.loads(existing)
        except json.JSONDecodeError:
            logger.warning(f"memory_bank JSON 解析失败: {existing}")
            memory_bank = []
    else:
        memory_bank = []

    # 追加新事件
    memory_bank.extend(events)

    # 保存回 Redis
    redis_client.set(memory_bank_key, json.dumps(memory_bank, ensure_ascii=False))
    logger.debug(f"已追加 {len(events)} 条事件到 memory_bank")

async def _compress_memory_bank_if_needed(user_id: str, llm_id: str) -> None:
    """
    如果 memory_bank 过长，执行压缩

    Args:
        user_id: 用户 ID
        llm_id: LLM ID
    """
    memory_bank_key = build_memory_key(LLMChatConstant.MEMORY_BANK, user_id, llm_id)

    # 获取当前 memory_bank
    existing = redis_client.get(memory_bank_key)
    if not existing:
        return

    try:
        memory_bank = json.loads(existing)
    except json.JSONDecodeError:
        logger.warning(f"memory_bank JSON 解析失败: {existing}")
        return

    # 检查是否需要压缩
    if len(memory_bank) < MEMORY_BANK_MAX_SIZE:
        return

    logger.info(f"memory_bank 长度 {len(memory_bank)} 超过阈值 {MEMORY_BANK_MAX_SIZE}，开始压缩...")

    # 构建压缩 prompt
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

    # 调用 LLM 压缩
    llm = await llm_model.get_memory_model()
    compressed = await llm.ainvoke(compress_prompt)

    # 解析并保存
    try:
        compressed_memory_bank = json.loads(compressed)
        redis_client.set(memory_bank_key, json.dumps(compressed_memory_bank, ensure_ascii=False))
        logger.info(f"memory_bank 压缩完成: {len(memory_bank)} -> {len(compressed_memory_bank)} 条")
    except json.JSONDecodeError:
        logger.warning(f"memory_bank 压缩 JSON 解析失败: {compressed}")

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

    # 1. 原有逻辑：总结消息存入 Chroma
    chain = await _build_summary_chain()
    summary_msg = await chain.ainvoke({
        "chat_history_msg": recent_msg_list,
        "recent_msg_list": recent_msg_list
    })

    documents = loader_util.load_file(summary_msg, FileTypeConstant.STR)
    source_id = user_id + llm_id + summary_msg
    await chroma_util.upload(ChromaTypeConstant.CHAT, documents, source_id, user_id=user_id, llm_id=llm_id)

    # 2. 新增：从对话中提取事件
    events = await _extract_memory_events(recent_msg_list)

    # 3. 新增：追加到 memory_bank
    if events:
        await _append_to_memory_bank(events, user_id, llm_id)

    # 4. 新增：检查并压缩（如需要）
    await _compress_memory_bank_if_needed(user_id, llm_id)

    # 5. 新增：更新 user_profile
    await _update_user_profile_in_compress(user_id, llm_id, recent_msg_list)

async def chat_msg(msg: ChatMsgTo, background_tasks: BackgroundTasks, request: Request) -> List[MessageBlock]:
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

    # 加载角色灵魂（Soul）
    soul = await PromptManager.get_soul("soul")

    logger.debug("api调用llm最后提示")
    chat_response = await chain.ainvoke({
        "soul": soul if soul else "",
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

    # 先去除 think 标签，再解析 action 标签，返回结构化消息块
    clean_response = strip_think_tags(chat_response)
    message_blocks = parse_action_tags(clean_response)

    return message_blocks


# =============== User Profile 更新相关函数 ===============

PROFILE_REQUIRED_DIMENSIONS = ["核心身份", "核心性格", "语言风格", "互动模式", "价值观", "长期兴趣", "绝对边界"]


async def _get_user_profile(user_id: str, llm_id: str) -> Optional[Dict]:
    """从 Redis 获取用户画像数据。
    
    根据用户ID和LLM ID构建缓存键，从Redis中查询并解析用户画像。
    
    Args:
        user_id: 用户唯一标识
        llm_id: LLM实例标识
        
    Returns:
        用户画像字典对象，查询失败或不存在时返回 None
    """
    profile_key = build_memory_key(LLMChatConstant.USER_PROFILE, user_id, llm_id)
    profile_json = redis_client.get(profile_key)
    
    if not profile_json:
        logger.debug(f"user_profile 不存在: user_id={user_id}, llm_id={llm_id}")
        return None
    
    try:
        profile = json.loads(profile_json)
        logger.debug(f"成功获取 user_profile: user_id={user_id}")
        return profile
    except json.JSONDecodeError as e:
        logger.error(f"user_profile JSON 解析失败: {e}, user_id={user_id}")
        return None


async def _save_user_profile(profile: Dict, user_id: str, llm_id: str) -> bool:
    """将用户画像数据持久化到 Redis。
    
    将更新后的用户画像序列化为JSON并存储到Redis缓存中。
    
    Args:
        profile: 用户画像字典对象
        user_id: 用户唯一标识
        llm_id: LLM实例标识
        
    Returns:
        保存成功返回 True，失败返回 False
    """
    try:
        profile_key = build_memory_key(LLMChatConstant.USER_PROFILE, user_id, llm_id)
        redis_client.set(profile_key, json.dumps(profile, ensure_ascii=False))
        logger.info(f"user_profile 更新成功: user_id={user_id}, llm_id={llm_id}")
        return True
    except Exception as e:
        logger.error(f"user_profile 保存失败: {e}, user_id={user_id}")
        return False


async def _build_profile_updater_chain():
    """获取用户画像更新 Chain（带缓存）。
    
    整合 Prompt 模板和 LLM 模型，构建用于更新用户画像的 LangChain Chain。
    首次调用时创建 Chain 并缓存，后续调用直接返回缓存对象。
    
    Returns:
        LangChain Runnable 对象，用于执行用户画像更新任务
        
    Raises:
        BusinessException: 当 Prompt 模板不存在时抛出业务异常
    """
    cache_key = "profile_updater"
    
    # 检查缓存
    if cache_key in _CHAIN_CACHE:
        logger.debug("使用缓存的 profile_updater Chain")
        return _CHAIN_CACHE[cache_key]
    
    # 缓存未命中，创建新 Chain
    prompt_template = await PromptManager.get_prompt("user_profile_updater")
    
    # 转义模板，防止JSON中的大括号与变量占位符冲突
    prompt_template = escape_template(prompt_template, ["current_profile", "chat_history"])
    
    if not prompt_template:
        from app.exception.BusinessException import BusinessException
        from app.common.constant.MsgStatusConstant import MsgStatusConstant
        raise BusinessException(MsgStatusConstant.RAG_MESSAGE_EXAM_ERROR)
    
    template = ChatPromptTemplate([("system", prompt_template)])
    llm = await llm_model.get_memory_json_model()
    chain = template | llm
    
    # 存入缓存
    _CHAIN_CACHE[cache_key] = chain
    logger.info("已创建并缓存 profile_updater Chain")
    
    return chain


def _validate_profile_structure(profile: Dict) -> bool:
    """验证用户画像结构完整性。
    
    检查用户画像是否包含所有必需的顶层维度字段。
    
    Args:
        profile: 待验证的用户画像字典
        
    Returns:
        结构验证通过返回 True，缺少必需字段时返回 False
    """
    missing_dims = [d for d in PROFILE_REQUIRED_DIMENSIONS if d not in profile]
    if missing_dims:
        logger.warning(f"user_profile 缺少维度: {missing_dims}")
        return False
    return True


async def _update_user_profile(
    current_profile: Dict,
    recent_msg_list: List[str]
) -> Optional[Dict]:
    """基于对话历史更新用户画像。
    
    通过 LLM 分析最近的对话内容，提取用户特征并更新画像。
    
    Args:
        current_profile: 当前用户画像字典
        recent_msg_list: 最近对话历史列表
        
    Returns:
        更新后的用户画像，校验失败或无更新时返回原始 profile，异常时返回 None
    """
    if not recent_msg_list:
        logger.debug("对话历史为空，跳过 user_profile 更新")
        return current_profile
    
    try:
        chain = await _build_profile_updater_chain()
        
        result = await chain.ainvoke({
            "current_profile": json.dumps(current_profile, ensure_ascii=False),
            "chat_history": "\n".join(recent_msg_list)
        })
        
        updated_profile = json.loads(result.content)
        
        if not _validate_profile_structure(updated_profile):
            logger.warning("user_profile 更新后的结构不完整，保留原数据")
            return current_profile
        
        logger.info("user_profile 已更新")
        return updated_profile
        
    except json.JSONDecodeError as e:
        logger.error(f"user_profile 更新失败: JSON 解析错误 - {e}")
        return current_profile
    except Exception as e:
        logger.error(f"user_profile 更新失败: {e}")
        return current_profile


async def _update_user_profile_in_compress(
    user_id: str, 
    llm_id: str, 
    recent_msg_list: List[str]
) -> None:
    """在消息压缩流程中协调用户画像更新。
    
    作为后台任务的一部分，负责查询、更新和保存用户画像的完整流程。
    当消息数量达到压缩阈值时被调用。
    
    使用 asyncio.gather 实现查询 profile 和构建 LLM Chain 的并行执行，
    减少等待时间，提升整体响应速度。
    
    Args:
        user_id: 用户唯一标识
        llm_id: LLM实例标识
        recent_msg_list: 待处理的最近对话历史列表
    """
    if not recent_msg_list:
        logger.debug(f"最近消息列表为空，跳过 user_profile 更新: user_id={user_id}")
        return
    
    try:
        # 并行执行：查询 profile 和构建 LLM Chain（无依赖关系，可同时进行）
        current_profile, chain = await asyncio.gather(
            _get_user_profile(user_id, llm_id),
            _build_profile_updater_chain()
        )
        
        if not current_profile:
            logger.info(f"当前 user_profile 不存在，无法更新: user_id={user_id}")
            return
        
        logger.debug(f"开始更新 user_profile: user_id={user_id}")
        
        # 调用 LLM 更新画像
        result = await chain.ainvoke({
            "current_profile": json.dumps(current_profile, ensure_ascii=False),
            "chat_history": "\n".join(recent_msg_list)
        })
        
        try:
            updated_profile = json.loads(result.content)
        except json.JSONDecodeError as json_err:
            logger.error(f"JSON 解析失败: {str(json_err)[:100]}, LLM返回: {str(result.content)[:200]}")
            return
        
        if not _validate_profile_structure(updated_profile):
            logger.warning("user_profile 更新后的结构不完整，保留原数据")
            return
        
        # 保存更新后的画像
        success = await _save_user_profile(updated_profile, user_id, llm_id)
        if success:
            logger.info(f"user_profile 更新成功: user_id={user_id}")
        else:
            logger.error(f"user_profile 保存失败: user_id={user_id}")
            
    except Exception as e:
        logger.error(f"user_profile 更新过程中发生错误: {str(e)[:200]}")
        logger.debug(f"user_id={user_id}, 异常类型: {type(e).__name__}")

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