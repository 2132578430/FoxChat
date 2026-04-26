from typing import List

import dashscope
from dashscope import TextEmbedding
from flashrank import Ranker
from langchain_community.document_compressors import FlashrankRerank
from langchain_ollama import OllamaEmbeddings, ChatOllama
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import SecretStr

from app.core.settings import global_settings

bge_m3_embed = OllamaEmbeddings(
    model="bge-m3",
)

qwen3_embed = OllamaEmbeddings(
    model = "qwen3-embedding:0.6b"
)

class DashScopeEmbeddings:
    """
    自定义向量类
    由于封装的向量类都是会自动进行分词器分词，因此需要我们手写一个向量类
    同时，传入的文本和集合可以进行一次清洗然后转化为向量
    本质其实是写一个OpenAiEmbeddings类，来实现我们后面的类需求
    DashScopeEmbeddings是通义封装的类
    """
    def __init__(self):
        self.model_name = "text-embedding-v4"
        dashscope.api_key = global_settings.key.qwen_model

    def _embed_single(self, text: str) -> List[float]:
        clean_text = text.strip()
        if not clean_text:
            return []

        res = TextEmbedding.call(
            model=self.model_name,
            input=clean_text,
        )

        if res.status_code != 200:
            return []

        embeddings = res.output.get("embeddings", [])
        if not embeddings:
            return []

        return embeddings[0].get("embedding", [])

    def embed_documents(self, text: list[str]) -> List[List[float]]:
        clean_list = [t.strip() for t in text if isinstance(t, str) and t.strip()]

        if not clean_list:
            return []

        res = TextEmbedding.call(
            model = self.model_name,
            input=clean_list,
        )

        if res.status_code != 200:
            return []

        embeddings = res.output.get("embeddings", [])
        return [item.get("embedding", []) for item in embeddings]

    def embed_query(self, text: str):
        return self._embed_single(text)

class ChromaModel:
    def __init__(self):
        self.embed_model = DashScopeEmbeddings()

    def embed(self, text: str | list):
        if isinstance(text, str):
            return self.embed_model.embed_query(text)
        elif isinstance(text, list):
            return self.embed_model.embed_documents(text)
        else:
            print("文档加载失败，非str与list")
            return []

    def embed_query(self, text: str):
        return self.embed(text)

    def embed_documents(self, text: list):
        return self.embed(text)

chroma_model = ChromaModel()

nomic_embed = OllamaEmbeddings(
    model="nomic-embed-text",
)

json_ds_model = ChatOpenAI(
    model="deepseek-v4-flash",
    api_key=SecretStr(global_settings.key.ds_model),
    base_url="https://api.deepseek.com",
    model_kwargs={
        "response_format": {
            "type": "json_object"
        }
    }
)

ds_model = ChatOpenAI(
    model="deepseek-v4-flash",
    api_key=SecretStr(global_settings.key.ds_model),
    base_url="https://api.deepseek.com",
)

kimi_model = ChatOpenAI(
    model = "moonshot-v1-8k",
    api_key = SecretStr(global_settings.key.kimi_model),
    base_url = "https://api.moonshot.cn/v1",
)

claude_opus_model = ChatOpenAI(
    model = "claude-opus-4.6",
    api_key=SecretStr(global_settings.key.claude_model),  # Claude API Key
    base_url="https://api.anthropic.com/v1"  # Claude 端点
)

qwen4b_model = ChatOllama(
    model="qwen3:4b ",
)

rerank_model = FlashrankRerank(model="ms-marco-MiniLM-L-12-v2",
                               top_n=20,
                               client=Ranker(model_name="ms-marco-MiniLM-L-12-v2"))

# MiniMax 模型配置
minimax_model = ChatOpenAI(
    model="MiniMax-M2.7",
    api_key=SecretStr(global_settings.key.minimax_model),
    base_url="https://api.minimax.chat/v1",
)
minimax_json_model = ChatOpenAI(
    model="MiniMax-M2.7",
    api_key=SecretStr(global_settings.key.minimax_model),
    base_url="https://api.minimax.chat/v1",
    model_kwargs={"response_format": {"type": "json_object"}}
)

# GLM 模型配置 (智谱AI GLM-4)
glm_model = ChatOpenAI(
    model="glm-5",
    api_key=SecretStr(global_settings.key.glm_model),
    base_url="https://open.bigmodel.cn/api/paas/v4",
)
glm_json_model = ChatOpenAI(
    model="glm-5",
    api_key=SecretStr(global_settings.key.glm_model),
    base_url="https://open.bigmodel.cn/api/paas/v4",
    model_kwargs={"response_format": {"type": "json_object"}}
)

LLM_MAP = {
    "ds_model": ds_model,
    "json_ds_model": json_ds_model,
    "kimi_model": kimi_model,
    "qwen4b_model": qwen4b_model,
    "minimax_model": minimax_model,
    "minimax_json_model": minimax_json_model,
    "claude_model": claude_opus_model,
    "glm_model": glm_model,
    "glm_json_model": glm_json_model,
}

async def get_llm_model(llm_name: str):
    """获取 LLM 模型实例。

    Args:
        llm_name: 模型名称，支持以下选项：
            - "default" 或空字符串: 返回配置的默认模型
            - "ds_model": DeepSeek 标准模型
            - "json_ds_model": DeepSeek JSON 输出模型
            - "kimi_model": Kimi 模型
            - "minimax_model": MiniMax 标准模型
            - "minimax_json_model": MiniMax JSON 输出模型
            - "qwen4b_model": Qwen 4B 本地模型
            - "glm_model": GLM 标准模型 (智谱AI)
            - "glm_json_model": GLM JSON 输出模型 (智谱AI)

    Returns:
        LLM 模型实例，如果模型不存在返回 None
    """
    # 如果未指定或为 "default"，使用配置的默认模型
    if not llm_name or llm_name == "default":
        llm_name = global_settings.model.default_llm
        logger.debug(f"使用默认模型: {llm_name}")
    elif llm_name == "default_json":
        llm_name = global_settings.model.default_json_llm
        logger.debug(f"使用默认 JSON 模型: {llm_name}")
    
    return LLM_MAP.get(llm_name)


def get_default_llm_name() -> str:
    """获取当前配置的默认 LLM 模型名称。
    
    Returns:
        默认模型名称字符串
    """
    return global_settings.model.default_llm


def get_default_json_llm_name() -> str:
    """获取当前配置的默认 JSON 输出 LLM 模型名称。

    Returns:
        默认 JSON 模型名称字符串
    """
    return global_settings.model.default_json_llm


def _resolve_model_name(config_name: str) -> str:
    """解析模型名称，处理默认值的回退逻辑。

    Args:
        config_name: 配置中的模型名称

    Returns:
        解析后的模型名称
    """
    if config_name == "default":
        return global_settings.model.default_llm
    elif config_name == "default_json":
        return global_settings.model.default_json_llm
    elif config_name == "default_embedding":
        return global_settings.model.default_embedding
    return config_name


async def get_chat_model():
    """获取聊天场景的 LLM。

    Returns:
        聊天模型实例
    """
    model_name = _resolve_model_name(global_settings.model_by_scenario.chat_llm)
    logger.debug(f"获取聊天模型: {model_name}")
    return LLM_MAP.get(model_name)


async def get_chat_json_model():
    """获取聊天 JSON 场景的 LLM。

    Returns:
        聊天 JSON 模型实例
    """
    model_name = _resolve_model_name(global_settings.model_by_scenario.chat_json_llm)
    logger.debug(f"获取聊天 JSON 模型: {model_name}")
    return LLM_MAP.get(model_name)


async def get_memory_model():
    """获取记忆场景的 LLM。

    Returns:
        记忆模型实例
    """
    model_name = _resolve_model_name(global_settings.model_by_scenario.memory_llm)
    logger.debug(f"获取记忆模型: {model_name}")
    return LLM_MAP.get(model_name)


async def get_memory_json_model():
    """获取记忆 JSON 场景的 LLM。

    Returns:
        记忆 JSON 模型实例
    """
    model_name = _resolve_model_name(global_settings.model_by_scenario.memory_json_llm)
    logger.debug(f"获取记忆 JSON 模型: {model_name}")
    return LLM_MAP.get(model_name)


async def get_summary_model():
    """获取消息总结场景的 LLM。

    Returns:
        总结模型实例
    """
    model_name = _resolve_model_name(global_settings.model_by_scenario.summary_llm)
    logger.debug(f"获取总结模型: {model_name}")
    return LLM_MAP.get(model_name)


async def get_extraction_model():
    """获取信息抽取场景的 LLM。

    Returns:
        抽取模型实例
    """
    model_name = _resolve_model_name(global_settings.model_by_scenario.extraction_llm)
    logger.debug(f"获取抽取模型: {model_name}")
    return LLM_MAP.get(model_name)


async def get_emotion_model():
    """获取情绪分类场景的 LLM。

    Returns:
        情绪分类模型实例
    """
    model_name = global_settings.model.emotion_llm
    logger.debug(f"获取情绪分类模型: {model_name}")
    return LLM_MAP.get(model_name)

