import asyncio
import json
from loguru import logger
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.common.constant.LLMChatConstant import LLMChatConstant, build_memory_key
from app.core import redis_client
from app.core.llm_model.model import LLM_MAP
from app.core.prompts.prompt_manager import PromptManager


def _escape_template(template: str, var_names: list[str]) -> str:
    """
    模板脱离方法
    当JSON和提示词注入字段冲突时，需要先将提示词中的JSON格式转义出来
    """
    for name in var_names:
        template = template.replace(f"{{{name}}}", f"__VAR_{name}__")

    template = template.replace("{", "{{").replace("}", "}}")

    for name in var_names:
        template = template.replace(f"__VAR_{name}__", f"{{{name}}}")

    return template


async def _call_llm(prompt_template: str, variables: dict, model_name: str = "ds_model") -> str:
    """
    调用模型

    Args:
        prompt_template: 提示词模板文件名（不含.md后缀）
        variables: 模板变量字典，如 {"raw_experience": "用户输入内容"}
        model_name: 模型名称，默认使用 ds_model

    Returns:
        模型返回的字符串结果
    """
    prompt_str = await PromptManager.get_prompt(prompt_template)
    # 去除模板语法，防止语法冲突
    prompt_str = _escape_template(prompt_str, list(variables.keys()))
    prompt = ChatPromptTemplate.from_messages([("human", prompt_str)])

    llm = LLM_MAP.get(model_name)
    output_parser = StrOutputParser()
    chain = prompt | llm | output_parser

    return await chain.ainvoke(variables)


async def _extract_core_anchor(experience: str) -> str:
    """
    生成角色核心
    """
    return await _call_llm("role_memory_core.md", {"raw_experience": experience})


async def _generate_user_profile(experience: str) -> dict:
    """
    生成用户画像
    """
    result = await _call_llm("user_profile.md", {"raw_experience": experience}, "json_ds_model")
    try:
        return json.loads(result)
    except json.JSONDecodeError as e:
        logger.error(f"用户画像 JSON 解析失败: {e}, 原始输出: {result}")
        return {}


async def _extract_initial_events(experience: str) -> list:
    """
    生成关键事件或状态
    """
    result = await _call_llm("memory_event_extractor.md", {"raw_experience": experience}, "json_ds_model")
    try:
        return json.loads(result)
    except json.JSONDecodeError as e:
        logger.error(f"初始事件 JSON 解析失败: {e}, 原始输出: {result}")
        return []


async def _generate_character_card(experience: str) -> dict:
    """
    生成角色卡（基于 SillyTavern 结构）
    """
    result = await _call_llm("character_card.md", {"raw_experience": experience}, "json_ds_model")
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

    # 存入原始记忆
    raw_key = build_memory_key(LLMChatConstant.RAW_EXPERIENCE, user_id, llm_id)
    redis_client.set(raw_key, experience)

    # 并发执行四个记忆提取任务，等待全部完成
    core_anchor, user_profile, character_card, initial_events = await asyncio.gather(
        _process_memory_task("角色核心锚点", _extract_core_anchor, LLMChatConstant.CORE_ANCHOR, user_id, llm_id, experience),
        _process_memory_task("用户画像", _generate_user_profile, LLMChatConstant.USER_PROFILE, user_id, llm_id, experience, serialize_json=True),
        _process_memory_task("角色卡", _generate_character_card, LLMChatConstant.CHARACTER_CARD, user_id, llm_id, experience, serialize_json=True),
        _process_memory_task("初始事件", _extract_initial_events, LLMChatConstant.MEMORY_BANK, user_id, llm_id, experience, serialize_json=True),
    )

    logger.info(f"用户 {user_id} 的初始记忆处理完成")