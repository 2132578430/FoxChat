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
    model="deepseek-chat",
    api_key=SecretStr(global_settings.key.ds_model),
    base_url="https://api.deepseek.com",
    model_kwargs={
        "response_format": {
            "type": "json_object"
        }
    }
)

ds_model = ChatOpenAI(
    model="deepseek-chat",
    api_key=SecretStr(global_settings.key.ds_model),
    base_url="https://api.deepseek.com"
)

kimi_model = ChatOpenAI(
    model = "moonshot-v1-8k",
    api_key = SecretStr(global_settings.key.kimi_model),
    base_url = "https://api.moonshot.cn/v1",
)

qwen4b_model = ChatOllama(
    model="qwen3:4b ",
)

rerank_model = FlashrankRerank(model="ms-marco-MiniLM-L-12-v2",
                               top_n=20,
                               client=Ranker(model_name="ms-marco-MiniLM-L-12-v2"))

# MiniMax 模型配置
# miniMax_model = ChatOpenAI(
#     model="MiniMax-Text-01",
#     api_key=SecretStr("your_minimax_api_key"),
#     base_url="https://api.minimax.chat/v1",
# )
# miniMax_json_model = ChatOpenAI(
#     model="MiniMax-Text-01",
#     api_key=SecretStr("your_minimax_api_key"),
#     base_url="https://api.minimax.chat/v1",
#     model_kwargs={"response_format": {"type": "json_object"}}
# )
# LLM_MAP["minimax_model"] = miniMax_model
# LLM_MAP["minimax_json_model"] = miniMax_json_model

LLM_MAP = {
    "ds_model": ds_model,
    "json_ds_model": json_ds_model,
    "kimi_model": kimi_model,
    "qwen4b_model": qwen4b_model,
}

async def get_llm_model(llm_name: str):
    logger.debug(f"key:{global_settings.key.ds_model}")
    return LLM_MAP.get(llm_name)

