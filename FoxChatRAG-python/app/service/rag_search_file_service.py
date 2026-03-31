
import os
from collections import defaultdict
from langchain_core.documents import Document

from app.chroma import rag_chroma
from app.util.chroma_util import get_search_retriever
from app.schemas.M import M
from app.schemas.rag_search_file_msg import RagSearchFileMsg

def search_file(msg: M[RagSearchFileMsg]):
    """
    文件搜索主逻辑
    :param msg:
    :return:
    """
    search_data:RagSearchFileMsg = msg.data
    search_msg = search_data.msg
    user_id = search_data.userId

    # TODO:校验消息是否丢失
    # if not verify_msg(msg):
    #     raise BusinessException(MsgStatusConstant.RAG_MESSAGE_EXAM_ERROR)
    # 向量数据库操作件
    retriever = get_search_retriever(rag_chroma, {"user_id": user_id})

    # 向量数据库初查结果
    documents = retriever.invoke(search_msg)

    # 文件路径归纳
    file_path_group = file_group_path(documents)

    # TODO:去除返回值
    return file_path_group

def file_group_path(documents: list[Document]):
    """
    # 文档路径归纳
    :param documents:
    :return:
    """
    file_group = defaultdict(list)
    file_max_score = defaultdict(float)
    file_group_names = {}

    for doc in documents:
        file_path = doc.metadata.get("file_path")
        file_name = doc.metadata.get("file_name", os.path.basename(file_path))
        score = float(doc.metadata.get("relevance_score", 0))

        file_group[file_path].append(doc.page_content)
        # 存储文件名
        file_group_names[file_path] = file_name

        if score > file_max_score[file_path]:
            file_max_score[file_path] = score

    file_group_arr = []

    for file_path, page_content in file_group.items():

        file_group_arr.append(
            {
                "filePath": file_path,
                "fileName": file_group_names[file_path],
                "score": file_max_score[file_path],
            }
        )
    return file_group_arr
