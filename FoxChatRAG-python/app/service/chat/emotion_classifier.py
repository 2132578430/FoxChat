"""
情绪分类服务模块

职责：
- 调用 LLM 分析模型回复的情绪状态
- 返回情绪标签和确定性
- 与 emotion_state 模块配合更新 Redis 状态
"""

import re

from langchain_core.prompts import PromptTemplate
from loguru import logger

from app.core.llm_model import get_emotion_model
from app.core.emotion_state import update_emotion_state, log_emotion_change
from app.util.template_util import escape_template

EMOTION_PROMPT_RAW = """分析角色回复的情绪状态，输出JSON格式结果。

角色回复：{model_reply}

任务：判断角色回复中流露的情绪，输出JSON：
{"emotion": "情绪词", "certainty": "确定或不确定"}

情绪选项：开心、悲伤、愤怒、惊讶、恐惧、厌恶、neutral
确定性选项：确定、不确定

规则：
- 只分析情绪，不要回复角色内容
- 只输出JSON，不要其他文字
- 根据语气、表情、动作描述判断情绪

输出："""

EMOTION_PROMPT = escape_template(EMOTION_PROMPT_RAW, ["model_reply"])


async def classify_emotion(model_reply: str) -> tuple[str, str]:
    """
    分析模型回复的情绪状态
    
    Args:
        model_reply: 模型的回复文本
    
    Returns:
        (emotion_label, certainty): 情绪标签和确定性
    """
    try:
        llm = await get_emotion_model()
        if not llm:
            logger.warning("情绪分类模型未配置，跳过分类")
            return ("neutral", "不确定")
        
        prompt = PromptTemplate.from_template(EMOTION_PROMPT)
        chain = prompt | llm
        
        response = await chain.ainvoke({"model_reply": model_reply})
        result_text = response.content.strip()
        
        emotion, certainty = _parse_emotion_result(result_text)
        
        logger.info(f"【情绪分类】模型回复: \"{model_reply[:50]}...\" → {emotion}:{certainty}")
        
        return (emotion, certainty)
        
    except Exception as e:
        logger.error(f"情绪分类调用失败: {e}")
        return ("neutral", "不确定")


def _parse_emotion_result(result_text: str) -> tuple[str, str]:
    """
    解析 LLM 返回的情绪分类结果
    
    Args:
        result_text: LLM 返回的文本
    
    Returns:
        (emotion, certainty): 解析后的情绪和确定性
    """
    import json
    
    text = result_text.strip()
    
    text = re.sub(r'<思索>.*?</思索>', '', text, flags=re.DOTALL).strip()
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    lines = text.split('\n')
    clean_lines = [line for line in lines if not line.strip().startswith('思索') and not line.strip().startswith('think')]
    text = '\n'.join(clean_lines).strip()
    
    json_match = re.search(r'\{[^{}]*"emotion"[^{}]*\}', text)
    if json_match:
        try:
            json_str = json_match.group(0)
            result = json.loads(json_str)
            emotion = result.get("emotion", "neutral")
            certainty = result.get("certainty", "不确定")
            return (emotion, certainty)
        except json.JSONDecodeError:
            pass
    
    pattern1 = r"^([\u4e00-\u9fa5a-zA-Z]+)\s*[:：]\s*(确定|不确定)$"
    match = re.match(pattern1, text)
    
    if match:
        emotion = match.group(1)
        certainty = match.group(2)
        return (emotion, certainty)
    
    pattern2 = r"(开心|悲伤|愤怒|惊讶|恐惧|厌恶|neutral)"
    emotion_match = re.search(pattern2, text)
    certainty_match = re.search(r"(确定|不确定)", text)
    
    if emotion_match and certainty_match:
        return (emotion_match.group(1), certainty_match.group(1))
    
    if emotion_match:
        return (emotion_match.group(1), "不确定")
    
    logger.warning(f"情绪分类结果解析失败，原始输出: '{result_text[:200]}'")
    return ("neutral", "不确定")


async def classify_and_update_emotion(user_id: str, llm_id: str, model_reply: str) -> None:
    """
    分析情绪并更新状态（供后台任务调用）
    
    Args:
        user_id: 用户 ID
        llm_id: 模型 ID
        model_reply: 模型回复
    """
    try:
        emotion, certainty = await classify_emotion(model_reply)
        
        action = "updated" if certainty == "确定" else "skipped"
        
        update_emotion_state(user_id, llm_id, emotion, certainty, model_reply)
        log_emotion_change(user_id, llm_id, model_reply, emotion, certainty, action)
        
    except Exception as e:
        logger.error(f"情绪分类后台任务失败: {e}")