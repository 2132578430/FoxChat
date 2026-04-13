# FoxChat 🦊

一个基于 Spring Boot + Netty + Vue 3 的现代化即时通讯系统，集成 RAG 知识库能力。

---

## 🌟 项目架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        FoxChat                                   │
├─────────────────┬─────────────────────┬─────────────────────────┤
│   FoxChat-vue   │   FoxChat-java      │  FoxChatRAG-python     │
│   (前端/桌面端)  │   (后端API/网关)     │  (RAG 向量服务)         │
└─────────────────┴─────────────────────┴─────────────────────────┘
         │                   │                     │
         └───────────────────┴─────────────────────┘
                           │
              ┌────────────┼────────────┐
              │   MySQL    │   Redis    │
              │            │   RabbitMQ │
              │            │   Minio    │
              └────────────┴────────────┘
```

---

## 🛠️ 技术栈

| 项目 | 技术 | 端口 |
| :--- | :--- | :--- |
| **FoxChat-vue** | Vue 3 + Vite + Electron | 5173 (Web) |
| **FoxChat-java** | Spring Boot 3 + Netty + MySQL | 12000 (HTTP), 13000 (WebSocket) |
| **FoxChatRAG-python** | FastAPI + LangChain + ChromaDB | 8000 |

### 中间件
- **MySQL 8.0** - 持久化数据
- **Redis** - 缓存、会话、心跳
- **RabbitMQ** - 消息队列（异步向量处理）
- **Minio** - 对象存储（文件/图片）

---

## 🚀 快速启动

### 1. 启动中间件

```bash
docker-compose up -d
```

### 2. 启动后端 (FoxChat-java)

```bash
cd FoxChat-java
./gradlew bootRun
```

### 3. 启动 RAG 服务 (FoxChatRAG-python)

```bash
cd FoxChatRAG-python
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 4. 启动前端 (FoxChat-vue)

```bash
cd FoxChat-vue
npm install
npm run dev
```

### 5. 打包 Electron 桌面端

```bash
cd FoxChat-vue
npm run electron:build
```

---

## 📂 项目结构

```
FoxChat/
├── FoxChat-vue/           # Vue 3 前端 + Electron 桌面端
│   ├── src/
│   │   ├── api/          # API 接口
│   │   ├── components/   # 组件
│   │   ├── views/        # 页面视图
│   │   ├── utils/        # 工具函数
│   │   └── proto/        # Protobuf 定义
│   ├── electron/         # Electron 主进程
│   └── README.md
│
├── FoxChat-java/         # Spring Boot 后端
│   ├── foxChat-web/      # Controller 层
│   ├── foxChat-service/  # Service 业务层
│   ├── foxChat-netty/    # Netty WebSocket 服务
│   ├── foxChat-common/   # 公共模块
│   ├── foxChat-pojo/     # 实体类
│   └── README.md
│
├── FoxChatRAG-python/    # FastAPI RAG 服务
│   ├── app/
│   │   ├── api/          # API 路由
│   │   ├── service/      # 业务逻辑
│   │   ├── core/         # 核心配置
│   │   └── util/         # 工具
│   ├── store/            # Chroma 向量库
│   └── README.md
│
├── docker-compose.yml    # 中间件配置
└── README.md             # 本文件
```

---

## 📜 通信协议

### HTTP 响应格式

```java
R<T> {
    Integer code;   // 1000 成功
    String msg;     // 消息
    T data;         // 数据
}
```

### WebSocket 二进制协议 (16 Bytes)

| Magic (4B) | Ver (1B) | Serial (1B) | MsgType (2B) | Length (4B) | Reserved (4B) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `0xCAFEBABE` | `1` | `1` (PB) | `1101/1201...` | Body Size | `0x00` |

### MsgType 定义

| 类型 | 说明 |
| :--- | :--- |
| 1101 | 私聊消息 |
| 1201 | 群聊消息 |
| 1102 | 消息签收 |
| 1103 | 心跳 |
| 1104 | 添加好友申请 |
| 1105 | 好友申请通知 |
| 1106/1107 | 好友上/下线通知 |

---

## 🔐 敏感配置

以下文件包含密钥、密码等敏感信息，**请勿提交至 Git**（已列入 `.gitignore`）：

| 文件 | 说明 |
|------|------|
| `FoxChatRAG-python/.env` | LLM API Key、MySQL/Redis/RabbitMQ 密码 |
| `FoxChat-java/deploy/.env` | MySQL/Redis 密码 |
| `FoxChat-java/**/application-local.yml` | 数据库密码、JWT 密钥、MinIO、邮件授权码 |
| `FoxChat-vue/.env` | 本地 API 地址 |

首次配置时，参考各模块下的 `.env.example` 文件复制并填写实际值。

## 🔧 相关文档

- [FoxChat-vue 前端文档](./FoxChat-vue/README.md)
- [FoxChat-java 后端文档](./FoxChat-java/README.md)
- [FoxChatRAG-python RAG 文档](./FoxChatRAG-python/README.md)