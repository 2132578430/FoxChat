# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FoxChat is a full-stack instant messaging system with integrated RAG (Retrieval-Augmented Generation) knowledge base. It consists of three separate applications:

| Component | Language | Framework | Purpose |
|-----------|----------|-----------|---------|
| **FoxChat-vue** | JS/TS | Vue 3 + Vite + Electron | Frontend/desktop client |
| **FoxChat-java** | Java | Spring Boot 3 + Netty | Backend API + WebSocket server |
| **FoxChatRAG-python** | Python | FastAPI + LangChain + ChromaDB | RAG vector search service |

## Quick Start

```bash
# Start infrastructure (MySQL, Redis, Minio, RabbitMQ)
docker-compose up -d

# Start Java backend (ports 12000 HTTP, 13000 WebSocket)
cd FoxChat-java && ./gradlew bootRun

# Start RAG service (port 8000)
cd FoxChatRAG-python && uvicorn main:app --host 0.0.0.0 --port 8000

# Start Vue frontend (port 5173)
cd FoxChat-vue && npm run dev
```

## Build Commands

**FoxChat-java**: Uses Gradle with multi-module structure (`foxChat-web`, `foxChat-service`, `foxChat-netty`, `foxChat-common`, `foxChat-pojo`).

**FoxChat-vue**: `npm run dev` for development, `npm run electron:build` for desktop app packaging.

**FoxChatRAG-python**: No build step required. Install dependencies with `pip install -r requirements.txt`.

## Architecture

### Communication Protocol

**HTTP REST**: Returns `R<T>` JSON with `code`, `msg`, `data` fields.

**WebSocket Binary Protocol (16-byte header)**:
```
Magic(4B) + Ver(1B) + Serial(1B) + MsgType(2B) + Length(4B) + Reserved(4B)
0xCAFEBABE       0x01     0x01(PB)    1101/1201...  Body Size  0x00
```

**MsgType codes**: 1101=私聊, 1201=群聊, 1102=消息签收, 1103=心跳, 1104/1105=好友申请, 1106/1107=上下线通知

### RAG Architecture

- ChromaDB for vector storage, FlashRank (ms-marco-MiniLM-L-12-v2) for reranking
- RabbitMQ for async document processing (upload → queue → vectorization)
- Ollama for local LLM, plus support for DeepSeek/Kimi/Qwen/MiniMax/Claude APIs
- Scenario-based model routing: chat, memory, summary, extraction scenarios use different models

### Infrastructure

MySQL (3306), Redis (6379), RabbitMQ (5672/15672), Minio (9000/9001).

## Configuration

**FoxChat-java**: `src/main/resources/application.yaml` - database, Redis, RabbitMQ, Minio settings.

**FoxChatRAG-python**: Uses `.env` file. Copy from `.env.example`. Model selection via `MODEL__DEFAULT_LLM` and `MODEL_BY_SCENARIO__*` variables.

## Key Implementation Details

- **Java Netty**: Custom `ByteToMessageDecoder` / `MessageToByteEncoder` for binary protocol. Protobuf serialization.
- **Java Spring Security + JWT**: Used for REST API authentication.
- **Python RAG**: Global exception handling via `BusinessException` + `GlobalExceptionHandler`. All requests/responses include `msgId` MD5 hash for integrity.
- **Memory/Compression**: FoxChatRAG-python has event extraction and memory bank compression integrated into the summary flow.
