import json
import mimetypes
import os

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common import FileTypeConstant
from app.common.constant.ChromaTypeConstant import ChromaTypeConstant
from app.common.constant.LLMChatConstant import LLMChatConstant
from app.core.db.redis_client import redis_client
from app.core.net import download_file
from app.models.rag_file import RagFile
from app.util import loader_util, chroma_util


async def update_file_status(file_path: str, db: AsyncSession):
        sql = select(RagFile).where(RagFile.file_path == file_path)

        result = await db.execute(sql)

        rag_file = result.scalars().first()

        rag_file.status = 2

        await db.commit()

async def upload_file(body, db: AsyncSession):
    """
    # rag文件上传处理逻辑
    :return:
    """
    body = body.decode('utf-8')

    data_json: dict = json.loads(body)

    file_json: dict = data_json.get("data")

    file_path = file_json.get("filePath")
    user_id = file_json.get("userId")

    file_type, _ = mimetypes.guess_type(file_path)

    if file_path:
        try:
            # 将文件从网络加载到本地
            local_file_path = await download_file(file_path)
            logger.info("下载文件完成:" + local_file_path)

            # 利用加载器加载成documents
            documents = await loader_util.load_file(local_file_path, FileTypeConstant(file_type))
            logger.info("加载文件完成")

            # 分段然后上传到向量库
            await chroma_util.upload(ChromaTypeConstant.RAG,
                         documents,
                         file_path,
                         user_id=user_id,
                         file_path=file_path,
                         )

            # 修改数据库状态
            await update_file_status(file_path, db)
            logger.info("向量化文件完成:" + file_path)

        finally:
            # 清除文件
            if os.path.exists(local_file_path):
                os.remove(local_file_path)


async def chat_init(body):
    """
    # 模型经历词上传向量库
    """
    msg_json: dict = json.loads(body)
    data_json: dict = msg_json.get("data")

    user_id = data_json.get("userId") or data_json.get("userName")
    content = data_json.get("experience")
    llm_id = data_json.get("llmId")

    # 初始化记忆存入redis
    init_memory_key = LLMChatConstant.CHAT_MEMORY + user_id + ":" + llm_id + ":" + LLMChatConstant.INIT_MEMORY

    redis_client.set(init_memory_key, content)
