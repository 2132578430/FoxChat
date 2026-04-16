# 角色情绪分类系统设计文档

> 日期：2026-04-15
> 状态：方案 A（极简验证版）
> 目标：学习 LLM 情绪分类能力

***

## 1. 背景与目标

### 1.1 背景

当前项目（FoxChatRAG）有多层记忆系统（Soul、Core Anchor、Character Card、Memory Bank、User Profile），但缺少**动态情绪状态**机制。

用户希望通过学习情绪分类功能，掌握：

- LLM 的情绪分类能力
- 事件驱动的状态管理系统
- 后续可扩展的情感分类模型集成

### 1.2 核心目标

**学习目的**：验证 LLM 情绪分类在中文对话中的效果。

**非目标**（本期不实现）：

- 情绪状态不影响角色回复
- 复杂的衰减机制
- 专门的情绪分类模型

***

## 2. 设计方案

### 2.1 方案 A：极简验证版

```
用户输入
    ↓
情绪分类服务（调用 LLM）
    ↓
情绪状态更新逻辑
    ↓
记录到 Redis / 日志（仅观察用）
    ↓
主对话流程继续（不受影响）
```

### 2.2 情绪分类服务

**职责**：

- 封装情绪分类调用
- 支持多模型切换（通过配置）
- 返回情绪标签 + 确定性

**接口设计**：

```python
class EmotionResult:
    label: str      # 情绪标签：开心、悲伤、愤怒、neutral...
    certainty: str   # 确定性：确定/不确定

async def classify_emotion(text: str) -> EmotionResult:
    """情绪分类接口"""
    ...
```

### 2.3 情绪状态更新规则

```
情绪分类器返回结果
    ↓
certainty == "确定" ？
    ↓ yes       ↓ no
更新情绪状态    保持原情绪
```

**原因**：避免用户说中性话（如"好的"）导致角色情绪被强制变为 neutral。

### 2.4 情绪状态存储

```python
# Redis Key: chat:memory:{user_id}:{llm_id}:role_emotion_state
{
    "emotion": "开心",
    "certainty": "确定",
    "last_update": "2026-04-15T10:30:00",
    "last_trigger": "今天加班好累"
}
```

***

## 3. 模型配置

### 3.1 全局配置

```python
# settings.py
EMOTION_MODEL = os.getenv("EMOTION_MODEL", "minimax")
```

### 3.2 支持的模型

| 配置值       | 模型                       | 说明       |
| --------- | ------------------------ | -------- |
| `minimax` | MiniMax API              | 默认，中文支持好 |
| `qwen3b`  | Qwen2.5-3B-Instruct      | 本地，中文优化  |
| `qwen7b`  | Qwen2.5-7B-Instruct INT4 | 本地，能力强   |

### 3.3 模型切换

通过修改 `.env` 文件即可切换：

```bash
# .env
EMOTION_MODEL=minimax
```

***

## 4. 组件设计

### 4.1 新增文件

```
app/
├── service/
│   └── emotion_classifier.py    # 情绪分类服务（新增）
└── core/
    └── emotion_state.py          # 情绪状态管理（新增）
```

### 4.2 修改文件

```
app/
├── service/
│   └── chat_msg_service.py       # 调用情绪分类（修改）
├── core/
│   └── config.py                 # 添加 EMOTION_MODEL 配置（修改）
└── common/
    └── constant/
        └── LLMChatConstant.py     # 添加情绪相关常量（修改）
```

***

## 5. 日志与验证

### 5.1 日志格式

```
【情绪分类】输入: "今天加班好累" → 开心:确定 [确定性足够，更新状态]
【情绪分类】输入: "好的" → 开心:不确定 [确定性不足，保持状态: 开心]
```

### 5.2 Redis 记录

```json
{
    "timestamp": "2026-04-15T10:30:00",
    "user_input": "今天加班好累",
    "emotion_label": "开心",
    "certainty": "确定",
    "action": "updated"
}
```

### 5.3 验证方式

- 通过日志观察情绪分类是否合理
- 通过 Redis 检查情绪状态变化是否符合预期

***

## 6. 错误处理

| 异常情况       | 处理方式         |
| ---------- | ------------ |
| 模型调用失败     | 记录日志，保持原情绪状态 |
| 分类超时       | 跳过本次分类，不影响对话 |
| Redis 写入失败 | 跳过，不影响对话     |
| 解析结果失败     | 保持原情绪状态      |

***

## 7. 情绪分类 Prompt（minimax）

```markdown
判断用户输入的情绪，只输出一个词和确定性程度。

情绪选项：开心、悲伤、愤怒、惊讶、恐惧、厌恶、neutral

确定性选项：确定、不确定

规则：
- 如果用户输入有明显情绪，选择对应情绪 + 确定性
- 如果用户输入是中性表达，选择 neutral + 确定性
- 只输出"情绪:确定性"，不要其他内容

用户输入：{text}

输出：
```

**输出示例**：

- `开心:确定` → 高置信度，更新状态
- `开心:不确定` → 低置信度，保持原状态
- `neutral:确定` → 中性表达，更新为 neutral

**状态更新规则**：

```
情绪分类器返回结果
    ↓
确定性 == "确定" ？
    ↓ yes       ↓ no
更新情绪状态    保持原情绪
```

***

## 8. 后续扩展方向（本期不做）

| 方向          | 说明                 |
| ----------- | ------------------ |
| 情绪注入 Prompt | 情绪状态影响角色回复         |
| 衰减机制        | 时间驱动的情绪自然衰减        |
| 本地模型        | 集成 Qwen2.5-3B/7B   |
| 专门情感模型      | HuggingFace 情感分类模型 |

***

## 9. 实现检查清单

- [ ] 添加 `EMOTION_MODEL` 配置到 `config.py`
- [ ] 添加 Redis 常量到 `LLMChatConstant.py`
- [ ] 实现 `emotion_classifier.py` 情绪分类服务
- [ ] 实现 `emotion_state.py` 情绪状态管理
- [ ] 在 `chat_msg_service.py` 中集成调用
- [ ] 添加日志记录
- [ ] 测试中文情绪分类效果

