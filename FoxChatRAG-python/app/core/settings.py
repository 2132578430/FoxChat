from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import BaseModel

class ServerConfig(BaseModel):
    port:int = 8000


class RabbitMqConfig(BaseModel):
    host:str = "localhost"
    port:int = 5672
    user:str = "admin"
    password:str = "admin"
    rag_queue:str = "rag.queue"
    chat_queue:str = "chat.queue"

class MysqlConfig(BaseModel):
    host:str = "localhost"
    port:int = 3306
    user:str = "root"
    password:str = "root123"
    database:str = "FoxChat"

    @property
    def url(self) -> str:
        return f"mysql+aiomysql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}?charset=utf8mb4"

class RedisConfig(BaseModel):
    host:str = "localhost"
    port:int = 6379
    user:str = "default"
    password:str = ""
    db:int = 0

class ModelApiKey(BaseModel):
    ds_model:str = ""
    kimi_model:str = ""
    qwen_model:str = ""
    minimax_model:str = ""
    claude_model:str = ""
    glm_model:str = ""


class ModelConfig(BaseModel):
    default_llm: str = "ds_model"
    default_json_llm: str = "json_ds_model"
    default_embedding: str = "dashscope"


class ModelByScenario(BaseModel):
    chat_llm: str = "default"
    chat_json_llm: str = "default_json"
    memory_llm: str = "default"
    memory_json_llm: str = "default_json"
    summary_llm: str = "default"
    extraction_llm: str = "default"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_nested_delimiter="__",
    )

    rabbitmq: RabbitMqConfig = RabbitMqConfig()
    mysql: MysqlConfig = MysqlConfig()
    server: ServerConfig = ServerConfig()
    redis: RedisConfig = RedisConfig()
    key: ModelApiKey = ModelApiKey()
    model: ModelConfig = ModelConfig()
    model_by_scenario: ModelByScenario = ModelByScenario()

global_settings = Settings()
