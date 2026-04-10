import hashlib
from typing import List

import jieba
from flashrank import Ranker
from langchain_chroma import Chroma
from langchain_classic.retrievers import ContextualCompressionRetriever
from langchain_community.document_compressors import FlashrankRerank
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document

from app.chroma import CHROMA_MAP, easy_txt_splitter
from app.common.constant import ChromaTypeConstant
def _build_chroma_filter(metadata: dict | None) -> dict | None:
    """构建chroma过滤语法"""
    if not metadata:
        return None

    if len(metadata) == 1:
        return dict(metadata)

    return {
        "$and": [{key: value} for key, value in metadata.items()]
    }

async def delete(chroma_type: ChromaTypeConstant, **metadata):
    """删除元数据相关的chroma数据"""
    chroma: Chroma = CHROMA_MAP[chroma_type]

    chroma.delete(
        where=_build_chroma_filter(metadata)
    )

async def search(chroma_type: ChromaTypeConstant, msg_content:str, metadata: dict | None = None) -> List[Document]:
    """
    # 根据消息和元数据获取chroma中的字段
    """
    chroma: Chroma = CHROMA_MAP[chroma_type]

    documents: List[Document] = chroma.similarity_search(
        query=msg_content,
        k = 5,
        filter = _build_chroma_filter(metadata)
    )

    return documents

async def _split_chunk(documents: list[Document]) -> list[Document]:
    """
    对文档进行简单分段
    """
    documents: list[Document] = easy_txt_splitter.split_documents(documents)

    return documents

async def upload(chroma_type: ChromaTypeConstant, documents: list, source_id: str, **metadata):
    """
    # 向量库存入文档集合
    :param chroma_type: 向量数据库
    :param documents: 文档本体
    :param source_id: 文档唯一标识
    """
    chroma: Chroma = CHROMA_MAP[chroma_type]

    # 将消息唯一id的hash值作为id前缀，保证向量库存的文件不重复
    pre_id = hashlib.md5(source_id.encode('utf-8')).hexdigest()

    # 给每一个传入的文档添加元数据
    for document in documents:
        for key, value in metadata.items():
            document.metadata[key] = value

    chroma.add_documents(
        documents,
        ids=[pre_id + str(i) for i in range(len(documents))],
    )
