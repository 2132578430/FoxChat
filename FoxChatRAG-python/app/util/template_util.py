"""LangChain 模板工具模块。

提供 Prompt 模板与外部数据（如 JSON）的语法冲突解决方案。
"""

import re


def escape_template(template: str, var_names: list[str]) -> str:
    """模板转义方法，防止JSON和LangChain模板语法冲突。

    当JSON数据中的大括号与Prompt模板变量冲突时，
    先保护变量占位符，转义其他大括号，再恢复变量。

    Args:
        template: Prompt模板字符串
        var_names: 变量名列表，如 ["current_profile", "chat_history"]

    Returns:
        转义后的模板字符串

    Example:
        >>> template = "用户信息: {current_profile}\\n对话: {chat_history}"
        >>> escaped = escape_template(template, ["current_profile", "chat_history"])
        >>> # 现在 JSON 中的 {"name": "test"} 会被正确转义
    """
    for name in var_names:
        template = template.replace(f"{{{name}}}", f"__VAR_{name}__")

    template = template.replace("{", "{{").replace("}", "}}")

    for name in var_names:
        template = template.replace(f"__VAR_{name}__", f"{{{name}}}")

    return template


def strip_all_tags(content: str) -> str:
    """去除 LLM 返回的所有 XML 标签及其内容（如 <think>、<action> 等）
    
    Args:
        content: 原始内容
        
    Returns:
        清理后的纯文本内容
    """
    if not content:
        return ""
    
    # 去除 <think> 标签
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
    
    # 去除所有 XML 标签（如 <action>...</action>）
    content = re.sub(r'<[a-zA-Z]+>.*?</[a-zA-Z]+>', '', content, flags=re.DOTALL)
    
    # 去除单独的 XML 标签（如 <end_turn>）
    content = re.sub(r'<[a-zA-Z]+>', '', content)
    
    return content.strip()


def strip_think_only(content: str) -> str:
    """仅去除 LLM 返回的 <think> 标签，保留其他标签（如 <action>）
    
    Args:
        content: 原始内容
        
    Returns:
        清理后的内容（保留 action 等标签）
    """
    if not content:
        return ""
    
    # 只去除 <think> 标签
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
    
    return content.strip()
