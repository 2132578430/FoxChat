"""
记忆上传服务

职责：
- 处理用户初始记忆上传
- 并发生成多层记忆结构：
  - 角色核心锚点
  - 用户画像
  - 角色卡
  - 初始事件
"""

import asyncio
import json
import re
from loguru import logger
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.common.constant.LLMChatConstant import LLMChatConstant, build_memory_key
from app.core import redis_client
from app.core.llm_model.model import LLM_MAP
from app.core.prompts.prompt_manager import PromptManager
from app.util.template_util import escape_template


def _extract_json_array_text(raw_text: str) -> str:
    """从模型输出中提取 JSON 数组文本。"""
    if not raw_text:
        return ""

    text = raw_text.strip()
    text = re.sub(r"```(?:json)?", "", text)
    text = text.replace("```", "").strip()

    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return text
    return text[start:end + 1]


async def _call_llm(prompt_template: str, variables: dict, model_name: str = "ds_model") -> str:
    """
    调用模型

    Args:
        prompt_template: 提示词模板文件名（不含.md后缀）
        variables: 模板变量字典，如 {"input_content": "用户输入内容"}
        model_name: 模型名称，默认使用 ds_model

    Returns:
        模型返回的字符串结果
    """
    prompt_str = await PromptManager.get_prompt(prompt_template)
    prompt_str = escape_template(prompt_str, list(variables.keys()))
    prompt = ChatPromptTemplate.from_messages([
        ("system", prompt_str),
        ("human", "{input_content}")
    ])

    llm = LLM_MAP.get(model_name)
    output_parser = StrOutputParser()
    chain = prompt | llm | output_parser

    return await chain.ainvoke(variables)


async def _extract_core_anchor(experience: str) -> str:
    """
    生成角色核心
    """
    return await _call_llm("role_memory_core.md", {"input_content": experience})


async def _generate_user_profile(experience: str) -> dict:
    """
    生成用户画像
    """
    result = await _call_llm("user_profile.md", {"input_content": experience}, "json_ds_model")
    try:
        return json.loads(result)
    except json.JSONDecodeError as e:
        logger.error(f"用户画像 JSON 解析失败: {e}, 原始输出: {result}")
        return {}


async def _extract_initial_events(experience: str) -> list:
    """
    生成关键事件或状态
    """
    result = await _call_llm("memory_event_extractor.md", {"input_content": experience}, "json_ds_model")
    try:
        return json.loads(_extract_json_array_text(result))
    except json.JSONDecodeError as e:
        logger.error(f"初始事件 JSON 解析失败: {e}, 原始输出: {result}")
        return []


async def _generate_character_card(experience: str) -> dict:
    """
    生成角色卡（基于 SillyTavern 结构）
    """
    result = await _call_llm("character_card.md", {"input_content": experience}, "json_ds_model")
    try:
        return json.loads(result)
    except json.JSONDecodeError as e:
        logger.error(f"角色卡 JSON 解析失败: {e}, 原始输出: {result}")
        return {}


async def _process_memory_task(
    task_name: str,
    extractor_func,
    constant_key,
    user_id: str,
    llm_id: str,
    experience: str,
    serialize_json: bool = False
):
    """
    并发处理单个记忆任务
    """
    try:
        result = await extractor_func(experience)
        redis_key = build_memory_key(constant_key, user_id, llm_id)
        redis_client.set(redis_key, json.dumps(result, ensure_ascii=False) if serialize_json else result)
        logger.info(f"{task_name}已生成")
        return result
    except Exception as e:
        logger.error(f"{task_name}生成失败: {e}")
        default = {} if serialize_json else ""
        return default


async def chat_init(body: str):
    """
    模型经历上传 - 多层记忆架构（并发处理）
    """
    try:
        msg_json = json.loads(body)
    except json.JSONDecodeError as e:
        logger.error(f"请求体 JSON 解析失败: {e}")
        raise ValueError(f"Invalid JSON in request body: {e}")

    data_json = msg_json.get("data")
    user_id = data_json.get("userId")
    experience = data_json.get("experience")
    llm_id = data_json.get("llmId")

    if data_json is None or user_id is None or experience is None or llm_id is None:
        logger.error("接收初始记忆有误")
        raise ValueError("接收初始记忆有误")

    logger.info(f"开始处理用户 {user_id} 的初始记忆...")

    raw_key = build_memory_key(LLMChatConstant.RAW_EXPERIENCE, user_id, llm_id)
    redis_client.set(raw_key, experience)

    core_anchor, user_profile, character_card, initial_events = await asyncio.gather(
        _process_memory_task("角色核心锚点", _extract_core_anchor, LLMChatConstant.CORE_ANCHOR, user_id, llm_id, experience),
        _process_memory_task("用户画像", _generate_user_profile, LLMChatConstant.USER_PROFILE, user_id, llm_id, experience, serialize_json=True),
        _process_memory_task("角色卡", _generate_character_card, LLMChatConstant.CHARACTER_CARD, user_id, llm_id, experience, serialize_json=True),
        _process_memory_task("初始事件", _extract_initial_events, LLMChatConstant.MEMORY_BANK, user_id, llm_id, experience, serialize_json=True),
    )

    logger.info(f"用户 {user_id} 的初始记忆处理完成")