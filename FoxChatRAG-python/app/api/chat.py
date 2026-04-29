from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.params import Query
from loguru import logger

from app.schemas import ChatMsgTo
from app.schemas.M import M
from app.service.chat import process_chat_msg, clear_chat_memory
from app.service import super_chat_service
chat_router = APIRouter(prefix="/chat", tags=["chat"])

@chat_router.post("/msg")
async def chat_msg(chat_msg_to: ChatMsgTo, background_tasks: BackgroundTasks, request: Request):

    logger.info(f"接收到消息：{chat_msg_to}")

    result = await process_chat_msg(chat_msg_to, background_tasks)

    logger.info(f"收到回复：{result}")

    return M.get_msg(result)

@chat_router.post("/superMsg")
async def super_chat_msg(chat_msg_to: ChatMsgTo, background_tasks: BackgroundTasks, request: Request):

    logger.info(f"【导演模式】API层接收到请求：user_id={chat_msg_to.user_id}, llm_id={chat_msg_to.llm_id}, msg_content={chat_msg_to.msg_content}")

    # 调用导演模式专用 service 方法
    result = await super_chat_service.director_mode_chat(
        user_id=chat_msg_to.userId,
        llm_id=chat_msg_to.llmId,
        msg_content=chat_msg_to.msgContent,
        background_tasks=background_tasks
    )

    logger.info(f"【导演模式】API层返回结果：{result}")

    return M.get_msg(result)

@chat_router.post("/delete")
async def chat_delete(
        user_id: str = Query(..., alias="userId"),
        llm_id: str = Query(..., alias="llmId")
):
    await clear_chat_memory(user_id, llm_id)