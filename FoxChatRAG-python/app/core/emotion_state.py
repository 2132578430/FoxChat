"""
情绪状态管理模块

职责：
- 管理 Redis 中的情绪状态存储
- 提供情绪状态的读取和更新接口
"""

import json
from datetime import datetime

from loguru import logger

from app.common.constant.LLMChatConstant import LLMChatConstant, build_memory_key
from app.core.db.redis_client import redis_client


def get_emotion_state(user_id: str, llm_id: str) -> dict:
    """
    获取当前情绪状态
    
    Args:
        user_id: 用户 ID
        llm_id: 模型 ID
    
    Returns:
        情绪状态字典，如果不存在返回默认状态
    """
    key = build_memory_key(LLMChatConstant.ROLE_EMOTION_STATE, user_id, llm_id)
    state_json = redis_client.get(key)
    
    if state_json:
        try:
            return json.loads(state_json)
        except json.JSONDecodeError:
            logger.warning(f"情绪状态 JSON 解析失败: {key}")
    
    return {"emotion": "neutral", "certainty": "确定", "last_update": "", "last_trigger": ""}


def update_emotion_state(user_id: str, llm_id: str, emotion: str, certainty: str, trigger: str) -> None:
    """
    更新情绪状态（仅当 certainty == "确定" 时更新）
    
    Args:
        user_id: 用户 ID
        llm_id: 模型 ID
        emotion: 情绪标签
        certainty: 确定性
        trigger: 触发情绪变化的模型回复片段
    """
    if certainty != "确定":
        logger.info(f"【情绪分类】确定性不足，保持原状态: {emotion}")
        return
    
    key = build_memory_key(LLMChatConstant.ROLE_EMOTION_STATE, user_id, llm_id)
    state = {
        "emotion": emotion,
        "certainty": certainty,
        "last_update": datetime.now().isoformat(),
        "last_trigger": trigger[:100] if len(trigger) > 100 else trigger
    }
    
    redis_client.set(key, json.dumps(state))
    logger.info(f"【情绪分类】情绪状态更新: {emotion} [触发: {trigger[:50]}...]")


def log_emotion_change(user_id: str, llm_id: str, model_reply: str, emotion: str, certainty: str, action: str) -> None:
    """
    记录情绪变化日志到 Redis（供观察验证）
    
    Args:
        user_id: 用户 ID
        llm_id: 模型 ID
        model_reply: 模型回复
        emotion: 情绪标签
        certainty: 确定性
        action: 动作（updated / skipped）
    """
    key = build_memory_key(LLMChatConstant.ROLE_EMOTION_LOG, user_id, llm_id)
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "model_reply": model_reply[:100] if len(model_reply) > 100 else model_reply,
        "emotion_label": emotion,
        "certainty": certainty,
        "action": action
    }
    
    redis_client.lpush(key, json.dumps(log_entry))
    redis_client.ltrim(key, 0, 99)