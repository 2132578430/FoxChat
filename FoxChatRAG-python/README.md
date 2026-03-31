# FoxChatRAG (狐狸聊天室 RAG 后端)

Ciallo~ 欢迎来到 FoxChatRAG 的后端世界！本项目是一个基于 **FastAPI** 和 **LangChain** 构建的高性能 RAG (Retrieval-Augmented Generation) 知识库系统。

它专门为 **FoxChat** 即时通讯应用提供强大的文档处理、语义检索及大模型问答能力。

---

## 🌟 核心特性 (Core Features)

- **🚀 高性能异步框架**: 基于 FastAPI 构建，全面支持异步 IO。
- **📚 多格式文档支持**: 支持 `.docx`, `.pdf`, `.txt`, `.csv` 等多种格式的自动加载与切分。
- **🔍 两阶段检索架构**:
  - **初筛**: 使用 Chroma 向量数据库进行语义相似度搜索。
  - **精筛**: 集成 `FlashRank` (ms-marco-MiniLM-L-12-v2) 进行 Cross-Encoder 重排序。
- **🤖 本地模型集成**: 深度对接 Ollama，支持 Llama3、Qwen 等本地大模型。
- **📩 异步处理架构**: 集成 RabbitMQ 消息队列，实现文档上传与向量化的解耦处理。
- **🛠️ 完善的异常处理**: 仿 Java `@RestControllerAdvice` 风格的全局异常捕获机制。

---

## 🏗️ 项目结构 (Project Structure)

```text
FoxChatRAG/
├── app/
│   ├── api/            # 路由层：定义 RESTful 接口
│   ├── service/        # 业务层：核心 RAG 检索与文档处理逻辑
│   ├── core/           # 核心层：模型配置、Prompt 管理、日志配置
│   ├── chroma/         # 数据库层：Chroma 向量库操作与文本切分
│   ├── mq/             # 消息队列：RabbitMQ 生产者与消费者实现
│   ├── schemas/        # 数据模型：Pydantic 模型定义
│   ├── common/         # 公共模块：常量定义、业务异常类
│   └── exception/      # 异常处理：全局异常捕获逻辑
├── store/              # 持久化层：Chroma 数据库存储文件
├── main.py             # 入口文件：FastAPI 实例初始化与中间件配置
└── README.md           # 项目说明文档
```

---

## 🛠️ 技术栈 (Tech Stack)

- **语言**: Python 3.12+
- **Web 框架**: FastAPI
- **RAG 框架**: LangChain / LangChain-Classic
- **向量数据库**: ChromaDB
- **消息队列**: RabbitMQ (aio-pika)
- **模型推理**: Ollama
- **重排序**: FlashRank
- **日志记录**: Loguru

---

## 🚀 快速开始 (Quick Start)

### 1. 环境准备
确保已安装 Python 3.12+ 并配置好虚拟环境。

### 2. 模型下载 (国内加速)
本项目使用了 `FlashRank` 进行精筛，建议配置 HuggingFace 镜像以加速下载：
```python
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
```

### 3. 启动项目
```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

---

## 📝 开发指南 (Development Notes)

### 全局异常处理
项目使用 `BusinessException` 配合 `MsgStatusConstant` 进行业务错误管理。所有异常都会被 `GlobalExceptionHandler` 捕获并返回统一格式的 JSON。

### 消息 ID 校验
所有请求和响应均包含 `msgId`，通过 `hashlib.md5` 对数据包进行哈希校验，确保数据完整性。

---