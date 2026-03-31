from langchain_ollama import OllamaEmbeddings, ChatOllama
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import SecretStr

from app.core.settings import global_settings

class ChromaModel:
    def __init__(self):
        # 默认使用 Ollama 的 bge-m3 模型
        self.embed_model = OllamaEmbeddings(model="bge-m3")

    def embed(self, text: str | list):
        if isinstance(text, str):
            return self.embed_model.embed_query(text)
        elif isinstance(text, list):
            return self.embed_model.embed_documents(text)
        else:
            print("文档加载失败，非str与list")
            return None

    def embed_query(self, text: str):
        return self.embed(text)

    def embed_documents(self, text: list):
        return self.embed(text)

chroma_model = ChromaModel()

ds_model = ChatOpenAI(
    model="deepseek-reasoner",
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

LLM_MAP = {
    "ds_model": ds_model,
    "kimi_model": kimi_model,
    "qwen4b_model": qwen4b_model,
}

async def get_llm_model(llm_name: str):
    logger.debug(f"key:{global_settings.key.ds_model}")
    return LLM_MAP.get(llm_name)
