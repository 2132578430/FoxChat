from langchain_chroma import Chroma

from app.common.constant import ChromaTypeConstant
from app.core.llm_model import model

rag_chroma = Chroma(
    collection_name="rag_collection",
    embedding_function=model.chroma_model,
    persist_directory="./store/rag"
)

chat_chroma = Chroma(
    collection_name="chat_collection",
    embedding_function=model.chroma_model,
    persist_directory="./store/chat"
)

CHROMA_MAP = {
    ChromaTypeConstant.RAG: rag_chroma,
    ChromaTypeConstant.CHAT: chat_chroma,
}