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
from dataclasses import dataclass
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
from app.service.chat.emotion_classifier import classify_and_update_emotion
from app.util import chroma_util, strip_all_tags, strip_think_only
from fastapi import BackgroundTasks
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableLambda
from langchain_core.language_models.chat_models import BaseMessage
from langchain_core.messages import HumanMessage, AIMessage


@dataclass
class ChatMemories:
    """聊天所需的记忆数据容器"""
    init_memory: str
    recent_msg: List[str]
    character_card_json: str
    core_anchor_json: str
    user_profile_json: str
    memory_bank_json: str
    emotion_state_json: str


@dataclass
class ParsedMemories:
    """解析后的记忆数据容器"""
    character_card_examples: str
    character_card_detail: str
    role_declaration: str
    core_anchor_text: str
    user_profile_summary: str
    memory_bank_summary: str
    emotion_state: str

async def chat_msg(msg: ChatMsgTo, background_tasks: BackgroundTasks) -> List[MessageBlock]:
    """
    对话主流程

    流程：
    1. 批量获取各种记忆
    2. 解析记忆为可读文本
    3. 构建历史消息
    4. 调用 LLM
    5. 保存对话
    6. 触发后台任务
    7. 解析返回
    """
    user_id = msg.userId
    llm_id = msg.llmId
    msg_content = msg.msgContent

    # 1. 获取所有的记忆事件+人物卡+角色核心等等
    memories = await _fetch_all_memories(user_id, llm_id)

    # 2. 解析记忆
    parsed = _parse_all_memories(memories)

    # 3. 构建历史消息
    history_msg = await _build_history_message(memories.recent_msg)

    # 4. 调用 LLM
    recent_msg_key = _build_recent_msg_key(user_id, llm_id)
    chat_response = await _invoke_llm(
        parsed, history_msg, memories.init_memory, msg_content, user_id, llm_id
    )

    # 5. 保存对话
    msg_count = await _save_chat_to_redis(
        recent_msg_key, msg_content, chat_response
    )

    # 6. 触发后台任务
    background_tasks.add_task(async_summary_msg, recent_msg_key, msg_count, user_id, llm_id)
    pure_text = strip_all_tags(chat_response)
    background_tasks.add_task(classify_and_update_emotion, user_id, llm_id, pure_text)

    # 7. 解析返回
    clean_response = strip_think_only(chat_response)
    return parse_action_tags(clean_response)


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
        build_memory_key(LLMChatConstant.ROLE_EMOTION_STATE, user_id, llm_id),
        build_memory_key(LLMChatConstant.ROLE_EMOTION_LOG, user_id, llm_id),
    ]

    pip = redis_client.pipeline()
    for key in keys_to_delete:
        pip.delete(key)
    pip.execute()

    await chroma_util.delete(ChromaTypeConstant.CHAT, user_id=user_id, llm_id=llm_id)

async def _fetch_all_memories(user_id: str, llm_id: str) -> ChatMemories:
    """批量获取所有记忆数据"""
    init_memory_key = _build_init_memory_key(user_id, llm_id)
    recent_msg_key = _build_recent_msg_key(user_id, llm_id)

    pip = redis_client.pipeline()
    pip.get(init_memory_key)
    pip.lrange(recent_msg_key, 0, 29)
    pip.get(build_memory_key(LLMChatConstant.CHARACTER_CARD, user_id, llm_id))
    pip.get(build_memory_key(LLMChatConstant.CORE_ANCHOR, user_id, llm_id))
    pip.get(build_memory_key(LLMChatConstant.USER_PROFILE, user_id, llm_id))
    pip.get(build_memory_key(LLMChatConstant.MEMORY_BANK, user_id, llm_id))
    pip.get(build_memory_key(LLMChatConstant.ROLE_EMOTION_STATE, user_id, llm_id))

    result = pip.execute()

    return ChatMemories(
        init_memory=result[0] or "",
        recent_msg=result[1],
        character_card_json=result[2] or "",
        core_anchor_json=result[3] or "",
        user_profile_json=result[4] or "",
        memory_bank_json=result[5] or "",
        emotion_state_json=result[6] or "",
    )


def _parse_all_memories(memories: ChatMemories) -> ParsedMemories:
    """解析所有记忆数据"""
    character_card_examples, character_card_detail = _parse_character_card(memories.character_card_json)
    role_declaration, core_anchor_text = _parse_core_anchor(memories.core_anchor_json)
    user_profile_summary = _parse_user_profile(memories.user_profile_json)
    memory_bank_summary = _parse_memory_bank(memories.memory_bank_json)
    emotion_state = _parse_emotion_state(memories.emotion_state_json)

    return ParsedMemories(
        character_card_examples=character_card_examples,
        character_card_detail=character_card_detail,
        role_declaration=role_declaration,
        core_anchor_text=core_anchor_text,
        user_profile_summary=user_profile_summary,
        memory_bank_summary=memory_bank_summary,
        emotion_state=emotion_state,
    )


def _parse_character_card(json_str: str) -> tuple[str, str]:
    """解析角色卡，返回 (示例对话, 详细描述)"""
    if not json_str:
        return "", ""

    try:
        card = json.loads(json_str)
        examples = card.get("示例对话", "")

        parts = []
        if card.get("性格关键词"):
            parts.append(f"性格关键词：{card['性格关键词']}")
        if card.get("动作风格"):
            parts.append(f"动作风格：{card['动作风格']}")
        if card.get("常用动作"):
            parts.append(f"常用动作：{', '.join(card['常用动作'])}")
        if card.get("核心描述"):
            parts.append(f"核心描述：{card['核心描述']}")

        return examples, "\n".join(parts) if parts else ""
    except json.JSONDecodeError:
        logger.warning("角色卡JSON解析失败")
        return "", ""


def _parse_core_anchor(text: str) -> tuple[str, str]:
    """解析核心锚点，返回 (角色声明, 核心锚点文本)"""
    if not text:
        return "", ""

    role_declaration = ""
    core_anchor_text = ""

    role_match = re.search(r'【角色声明】\s*(.+?)(?=【角色核心锚点】|$)', text, re.DOTALL)
    if role_match:
        role_declaration = role_match.group(1).strip()

    anchor_match = re.search(r'【角色核心锚点】\s*(.+?)(?=【绝对边界】|$)', text, re.DOTALL)
    if anchor_match:
        core_anchor_text = anchor_match.group(1).strip()

    return role_declaration, core_anchor_text


def _parse_user_profile(json_str: str) -> str:
    """解析用户画像"""
    if not json_str:
        return ""

    try:
        profile = json.loads(json_str)
        parts = []
        for dim_key, dim_val in profile.items():
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
        return "\n".join(parts) if parts else ""
    except json.JSONDecodeError:
        logger.warning("user_profile JSON解析失败")
        return ""


def _parse_memory_bank(json_str: str) -> str:
    """解析 Memory Bank，只取最近5条作为保底"""
    if not json_str:
        return ""

    try:
        bank = json.loads(json_str)
        if not isinstance(bank, list) or not bank:
            return ""

        recent_items = bank[-5:] if len(bank) > 5 else bank
        logger.info(f"Memory Bank: 共 {len(bank)} 条，注入最近 {len(recent_items)} 条")

        lines = []
        for item in recent_items:
            time_val = item.get("time", "某时")
            content_val = item.get("content", "")
            type_val = item.get("type", "event")
            lines.append(f"- [{time_val}]（{type_val}）{content_val}")
        return "\n".join(lines)
    except json.JSONDecodeError:
        logger.warning("memory_bank JSON解析失败")
        return ""


def _parse_emotion_state(json_str: str) -> str:
    """解析情绪状态"""
    if not json_str:
        return "当前情绪：平静（默认状态）"

    try:
        state = json.loads(json_str)
        emotion = state.get("emotion", "neutral")
        certainty = state.get("certainty", "确定")
        last_trigger = state.get("last_trigger", "")
        
        emotion_map = {
            "neutral": "平静",
            "happy": "开心",
            "sad": "难过",
            "angry": "生气",
            "surprised": "惊讶",
            "fear": "害怕",
            "disgust": "厌恶",
            "anticipation": "期待",
            "trust": "信任",
            "love": "喜欢",
            "joy": "喜悦",
            "sadness": "悲伤",
            "anger": "愤怒",
            "fearful": "恐惧",
            "disgusted": "厌恶",
            "surprise": "惊讶",
            "anticipating": "期待",
            "trusting": "信任",
            "loving": "爱",
        }
        
        emotion_cn = emotion_map.get(emotion.lower(), emotion)
        result = f"当前情绪：{emotion_cn}"
        if last_trigger:
            result += f"（最近触发：{last_trigger[:50]}）"
        return result
    except json.JSONDecodeError:
        logger.warning("emotion_state JSON解析失败")
        return "当前情绪：平静（默认状态）"


# ==================== 私有函数 - LLM调用 ====================

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
    chain = template | RunnableLambda(_print_template) | llm | str_parser
    return chain


async def _invoke_llm(
    parsed: ParsedMemories,
    history_msg: List[BaseMessage],
    init_memory: str,
    msg_content: str,
    user_id: str,
    llm_id: str
) -> str:
    """调用 LLM 生成回复"""
    chain = await _build_chat_chain()
    soul = await PromptManager.get_soul("soul")

    relevant_memories = await _search_relevant_memories(msg_content, user_id, llm_id)

    logger.debug("api调用llm最后提示")
    return await chain.ainvoke({
        "soul": soul or "",
        "role_declaration": parsed.role_declaration,
        "core_anchor": parsed.core_anchor_text,
        "character_card": init_memory,
        "mes_example": parsed.character_card_examples,
        "character_card_detail": parsed.character_card_detail,
        "call_convention": "",
        "user_profile_summary": parsed.user_profile_summary,
        "memory_bank_summary": parsed.memory_bank_summary,
        "relevant_memories": relevant_memories,
        "emotion_state": parsed.emotion_state,
        "recent_chat": "",
        "history_msg": history_msg,
        "user_message": msg_content,
    })


async def _search_relevant_memories(msg_content: str, user_id: str, llm_id: str) -> str:
    """检索相关记忆"""
    try:
        documents = await chroma_util.search(
            ChromaTypeConstant.CHAT,
            msg_content,
            {"user_id": user_id, "llm_id": llm_id}
        )
        
        if not documents:
            return ""
        
        lines = []
        for doc in documents[:3]:
            content = doc.page_content.strip()
            if content:
                lines.append(f"- {content}")
        
        logger.info(f"检索到 {len(documents)} 条相关记忆，注入 {len(lines)} 条")
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"检索相关记忆失败: {e}")
        return ""


def _print_template(template_value):
    """打印注入的模板变量（调试用）"""
    logger.debug(f"注入消息：\n{template_value}")
    return template_value


# ==================== 私有函数 - 历史消息 ====================

async def _build_history_message(recent_msg: List[str]) -> List[BaseMessage]:
    """将 Redis 中的消息列表转换为 LangChain 的历史消息格式"""
    if not recent_msg:
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


# ==================== 私有函数 - Redis操作 ====================

def _build_init_memory_key(user_id: str, llm_id: str) -> str:
    """构建初始化记忆的 Redis key"""
    return LLMChatConstant.CHAT_MEMORY + user_id + ":" + llm_id + ":" + LLMChatConstant.INIT_MEMORY


def _build_recent_msg_key(user_id: str, llm_id: str) -> str:
    """构建最近消息的 Redis key"""
    return LLMChatConstant.CHAT_MEMORY + user_id + ":" + llm_id + ":" + LLMChatConstant.RECENT_MSG


async def _save_chat_to_redis(recent_msg_key: str, msg_content: str, chat_response: str) -> int:
    """保存对话到 Redis，返回消息总数"""
    pip = redis_client.pipeline()
    pip.lpush(recent_msg_key, json.dumps({"role": "human", "content": msg_content}))
    pip.lpush(recent_msg_key, json.dumps({"role": "ai", "content": chat_response}))
    pip.llen(recent_msg_key)
    result = pip.execute()
    return result[0]


# ==================== 公开函数 - 响应解析 ====================

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