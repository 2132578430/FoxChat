import os
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI

from app.api import rag_router, chat_router
from app.core.settings import global_settings
from app.core.mq import init_rabbitmq, close_rabbitmq
from app.exception import register_exception_handlers

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 开启rabbitmq监听后台
    connection = await init_rabbitmq()

    yield

    # 关闭rabbitmq监听后台
    await close_rabbitmq(connection)

load_dotenv()

app = FastAPI(lifespan=lifespan)

# 注册全局异常处理器
register_exception_handlers(app)

# 注册路由
app.include_router(rag_router)
app.include_router(chat_router)

server_port = global_settings.server.port

if __name__ == '__main__':
    uvicorn.run("main:app", host="0.0.0.0", port=server_port, reload=False)