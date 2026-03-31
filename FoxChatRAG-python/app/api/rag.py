from loguru import logger

from fastapi import APIRouter
from app.schemas.M import M
from app.schemas.rag_search_file_msg import RagSearchFileMsg
from app.service import rag_search_file_service

rag_router = APIRouter(prefix="/rag", tags=["rag"])

# 根据发送的消息检索文件
@rag_router.post("/searchFile")
async def search_file(msg: M[RagSearchFileMsg]):
    logger.info("接收到消息" + str(msg))

    result = rag_search_file_service.search_file(msg)

    return M.get_msg(data=result)