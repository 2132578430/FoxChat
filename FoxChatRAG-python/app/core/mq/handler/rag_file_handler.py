import aio_pika
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.service import upload_vector_service


async def rag_file_handler(message, db: AsyncSession):
    """
    # rag消息处理体
    :return:
    """
    try :
        # TODO:检查幂等性

        await upload_vector_service.upload_file(message.body, db)
        await message.ack()
    except Exception as e:
        logger.error(f"rag文件处理错误: {e}")
        await message.nack(requeue=True)