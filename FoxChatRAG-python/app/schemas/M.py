import hashlib
import json
from typing import TypeVar, Generic

from pydantic import BaseModel

T = TypeVar("T")

class M(BaseModel, Generic[T]):
    msgId: str
    data: T

    @classmethod
    def get_msg(cls, data: T, msg_id: str | None = None) -> "M[T]":
        if isinstance(data, dict | list):
            data_json = json.dumps(data, separators=(',', ':'))
        elif isinstance(data, BaseModel):
            data_json = data.model_dump_json()
        else:
            data_json = json.dumps(data)

        if not msg_id:
            msg_id = hashlib.md5(data_json.encode("utf-8")).hexdigest()

        return cls(data=data, msgId=msg_id)
