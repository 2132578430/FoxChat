from enum import Enum


class MsgStatusConstant(Enum):
    RAG_MESSAGE_EXAM_ERROR = (20000, "rag消息校验错误")

    def __init__(self, code: int, msg: str):
        self.code = code
        self.msg = msg