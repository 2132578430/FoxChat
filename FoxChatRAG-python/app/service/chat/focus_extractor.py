"""
话题焦点提取服务模块

职责：
- 调用 LLM 分析用户输入的话题焦点
- 返回焦点标签和置信度
- 与 current_state 模块配合更新 Redis 状态

与 emotion_classifier.py 的关系：
- 相同设计模式（小模型提取 + 直接更新）
- 相同调用位置（锁内同步 await）
- 相同错误处理（失败不中断主流程）
"""

import json
import re

from langchain_core.prompts import PromptTemplate
from loguru import logger

from app.core.llm_model.model import get_extraction_model
from app.core.prompts.prompt_manager import PromptManager
from app.service.chat.state_manager import update_current_state
from app.schemas.current_state import UpdateSource
from app.util.template_util import escape_template


async def classify_and_update_focus(
    user_id: str,
    llm_id: str,
    user_input: str,
    current_round: int = 0,
) -> None:
    """
    分析话题焦点并更新状态（供锁内同步调用）

    Args:
        user_id: 用户 ID
        llm_id: 模型 ID
        user_input: 用户输入文本
        current_round: 当前全局轮数
    """
    try:
        llm = await get_extraction_model()
        if not llm:
            logger.warning("话题焦点提取模型未配置，跳过提取")
            return

        prompt_text = await PromptManager.get_prompt("focus_extractor")
        prompt_text = escape_template(prompt_text, ["user_input"])
        prompt = PromptTemplate.from_template(prompt_text)
        chain = prompt | llm

        response = await chain.ainvoke({"user_input": user_input})
        result_text = response.content.strip()

        # 解析 JSON 结果
        result = _parse_focus_result(result_text)

        if result and result.get("confidence", 0) >= 0.7:
            focus = result.get("focus", "")
            confidence = result.get("confidence", 0)
            reason = result.get("reason", "")

            update_current_state(
                user_id=user_id,
                llm_id=llm_id,
                field_name="current_focus",
                new_value=focus,
                confidence=confidence,
                source=UpdateSource.RUNTIME,
                reason=f"LLM焦点提取: {reason}",
                current_round=current_round,
            )
            logger.info(f"【焦点更新】focus = {focus} (confidence={confidence})")
        else:
            logger.debug(f"【焦点保持】置信度不足，保持原状态")

    except Exception as e:
        logger.error(f"话题焦点提取失败: {e}")


def _parse_focus_result(result_text: str) -> dict | None:
    """
    解析 LLM 返回的焦点提取结果

    Args:
        result_text: LLM 返回的文本

    Returns:
        解析后的字典，包含 focus、confidence、reason
    """
    text = result_text.strip()

    # 清除思索标签
    text = re.sub(r'<思索>.*?</思索>', '', text, flags=re.DOTALL).strip()
    text = re.sub(r'itesi.*?itesi', '', text, flags=re.DOTALL).strip()

    # 提取 JSON
    json_match = re.search(r'\{[^{}]*\}', text)
    if json_match:
        try:
            json_str = json_match.group(0)
            result = json.loads(json_str)

            # 验证必需字段
            if "focus" in result and "confidence" in result:
                return result
        except json.JSONDecodeError as e:
            logger.warning(f"焦点提取 JSON 解析失败: {e}")

    logger.warning(f"焦点提取结果解析失败，原始输出: '{result_text[:200]}'")
    return None