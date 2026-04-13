# 模型配置设计方案（推荐）

## 核心理念

**"配置驱动 + 按域注册"** - 结合两者的优点：
- 配置文件中设置默认值
- 代码中按功能域注册
- 调用时简单直接

## 架构设计

```
settings.py (配置层)
├── ModelConfig
│   ├── default_llm: "ds_model"
│   ├── default_json_llm: "json_ds_model"
│   └── default_embedding: "dashscope"
│
└── ModelByScenario (按场景的配置)
    ├── chat_llm: "ds_model"
    ├── chat_json_llm: "json_ds_model"
    ├── memory_llm: "ds_model"
    ├── memory_json_llm: "json_ds_model"
    └── embedding: "dashscope"

model.py (实现层)
├── LLM_MAP (注册表)
│   ├── ds_model, json_ds_model
│   ├── kimi_model, minimax_model
│   └── ...
│
└── LLM_BY_SCENARIO (场景化获取)
    ├── get_chat_model()
    ├── get_chat_json_model()
    ├── get_memory_model()
    └── get_embedding_model()
```

## 实现示例

### 1. settings.py - 按场景配置

```python
class ModelByScenario(BaseModel):
    chat_llm: str = "default"      # 对话模型，默认使用全局默认
    chat_json_llm: str = "default_json"  # JSON 对话
    memory_llm: str = "default"     # 记忆总结
    memory_json_llm: str = "default_json"  # 记忆 JSON
    embedding: str = "default_embedding"  # 向量模型

class Settings(BaseSettings):
    # ... 其他配置 ...
    model: ModelConfig = ModelConfig()
    model_by_scenario: ModelByScenario = ModelByScenario()
```

### 2. .env 配置示例

```env
# 全局默认模型
MODEL__DEFAULT_LLM=ds_model
MODEL__DEFAULT_JSON_LLM=json_ds_model
MODEL__DEFAULT_EMBEDDING=dashscope

# 按场景覆盖（可选）
MODEL_BY_SCENARIO__CHAT_LLM=ds_model
MODEL_BY_SCENARIO__CHAT_JSON_LLM=json_ds_model
MODEL_BY_SCENARIO__MEMORY_LLM=ds_model
MODEL_BY_SCENARIO__MEMORY_JSON_LLM=json_ds_model
```

### 3. model.py - 场景化获取函数

```python
async def get_chat_model():
    """获取聊天场景的 LLM"""
    model_name = global_settings.model_by_scenario.chat_llm
    if model_name == "default":
        model_name = global_settings.model.default_llm
    return LLM_MAP.get(model_name)

async def get_chat_json_model():
    """获取聊天 JSON 场景的 LLM"""
    model_name = global_settings.model_by_scenario.chat_json_llm
    if model_name == "default_json":
        model_name = global_settings.model.default_json_llm
    elif model_name == "default":
        model_name = global_settings.model.default_llm
    return LLM_MAP.get(model_name)

async def get_memory_model():
    """获取记忆场景的 LLM"""
    model_name = global_settings.model_by_scenario.memory_llm
    if model_name == "default":
        model_name = global_settings.model.default_llm
    return LLM_MAP.get(model_name)

async def get_memory_json_model():
    """获取记忆 JSON 场景的 LLM"""
    model_name = global_settings.model_by_scenario.memory_json_llm
    if model_name == "default_json":
        model_name = global_settings.model.default_json_llm
    elif model_name == "default":
        model_name = global_settings.model.default_llm
    return LLM_MAP.get(model_name)
```

### 4. 代码中使用

```python
# chat_msg_service.py
async def _build_profile_updater_chain():
    # 使用场景化获取
    llm = await get_memory_json_model()
    # 而不是
    # llm = await get_llm_model("json_ds_model")
```

## 优势

### 1. **零代码切换**
```bash
# .env 中改一行
MODEL_BY_SCENARIO__MEMORY_LLM=minimax_model
```

### 2. **调用简单**
```python
# 直接用，不用记模型名
llm = await get_memory_json_model()
```

### 3. **灵活性高**
```python
# 可以全局统一
MODEL__DEFAULT_LLM=ds_model

# 也可以按场景覆盖
MODEL_BY_SCENARIO__CHAT_LLM=kimi_model
MODEL_BY_SCENARIO__MEMORY_LLM=minimax_model
```

### 4. **生产级特性**
- ✅ 环境隔离（dev/staging/prod）
- ✅ 类型安全（IDE 补全）
- ✅ 配置集中
- ✅ 易于测试（可以 mock）

## 与现有方案对比

| 特性 | 纯配置驱动 | 纯硬编码 | 混合方案（推荐） |
|------|-----------|---------|-----------------|
| 切换模型 | 改代码 | 改代码 | 改配置 |
| 调用简洁性 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| 灵活性 | ⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐⭐ |
| 可维护性 | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 业界使用度 | ⭐⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐ |

## 总结

**这个混合方案**是业界的主流做法，平衡了：
- 灵活性（可配置）
- 简洁性（简单调用）
- 可维护性（集中管理）
- 扩展性（易于添加新场景）

建议采用这个方案！
