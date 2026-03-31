import hashlib
from typing import List

from flashrank import Ranker
from langchain_chroma import Chroma
from langchain_classic.retrievers import ContextualCompressionRetriever
from langchain_community.document_compressors import FlashrankRerank
from langchain_core.documents import Document

from app.chroma import CHROMA_MAP, documents_splitter
from app.common.constant import ChromaTypeConstant

async def delete(chroma_type: ChromaTypeConstant, **metadata):
    chroma: Chroma = CHROMA_MAP[chroma_type]

    param = {
        "$and": [{key, value} for key, value in metadata.items()]
    }

    chroma.delete(
        where=param
    )

async def search(chroma_type: ChromaTypeConstant, msg_content:str, metadata: dict | None = None) -> List[Document]:
    """
    # 根据消息和元数据获取chroma中的字段
    """
    chroma: Chroma = CHROMA_MAP[chroma_type]

    param = {
        "$and": [{key: value} for key, value in metadata.items()]
    }

    documents: List[Document] = chroma.similarity_search(
        query=msg_content,
        k = 5,
        filter = param
    )

    return documents



async def upload(chroma_type: ChromaTypeConstant, documents: list, source_id: str, **metadata):
    """
    # 向量库存入文件
    :param chroma_type: 向量数据库
    :param documents: 文档本体
    :param source_id: 文档唯一标识
    """
    chroma: Chroma = CHROMA_MAP[chroma_type]

    # 将消息唯一id作为hash值，保证向量库存的文件不重复
    pre_id = hashlib.md5(source_id.encode('utf-8')).hexdigest()

    # 对文档进行分段
    documents: list[Document] = documents_splitter.split_documents(documents)

    # 给每一个传入的文档添加元数据
    for document in documents:
        for key, value in metadata.items():
            document.metadata[key] = value

    chroma.add_documents(
        documents,
        ids=[pre_id + str(i) for i in range(len(documents))],
    )


def get_search_retriever(chroma: Chroma, metadata: dict | None = None):
    """
    # 获取上下文压缩模型
    :param chroma:
    :param metadata:
    :return:
    """
    retriever = chroma.as_retriever(
        search_type="similarity",
        search_kwargs={
            "k": 100,
            "filter": metadata
        }
    )

    rerank_model = FlashrankRerank(model="ms-marco-MiniLM-L-12-v2",
                                   top_n=20,
                                   client=Ranker(model_name="ms-marco-MiniLM-L-12-v2"))

    final_retriever = ContextualCompressionRetriever(
        base_compressor=rerank_model,
        base_retriever=retriever,
    )

    return final_retriever