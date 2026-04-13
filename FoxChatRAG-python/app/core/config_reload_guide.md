# 配置热更新方案

## 背景

用户问题：修改 `.env` 后如何让 `settings` 重新加载？

## 方案对比

### 方案1：重启应用（推荐）⭐⭐⭐⭐⭐

**原理**：`.env` 在应用启动时一次性加载到 `Settings` 对象，后续不会重新读取

**优点**：
- ✅ 简单可靠
- ✅ 无竞态条件
- ✅ 无需额外代码

**缺点**：
- ❌ 需要重启服务
- ❌ 正在处理的请求可能中断

**适用场景**：
- 开发环境
- 配置文件不常修改
- 可以接受短暂停机

**操作步骤**：
```bash
# 1. 修改 .env 文件
vim .env
# MODEL__DEFAULT_LLM=minimax_model

# 2. 重启应用
# Linux/Mac
pkill -f "python main.py"
python main.py

# Windows
taskkill /F /IM python.exe
python main.py
```

---

### 方案2：提供重载 API（开发环境）⭐⭐⭐

**原理**：添加一个管理接口，手动触发配置重新加载

**实现示例**：

```python
# app/api/admin.py
from fastapi import APIRouter
from app.core.settings import reload_settings

router = APIRouter(prefix="/admin", tags=["管理"])

@router.post("/config/reload")
async def reload_config():
    """重新加载配置（开发环境使用）"""
    reload_settings()
    return {"status": "success", "message": "配置已重载"}
```

```python
# app/core/settings.py
import importlib

def reload_settings():
    """重新加载配置（清除缓存，重新读取 .env）"""
    global global_settings
    # 清除 Pydantic Settings 的缓存
    importlib.reload(Settings)
    global_settings = Settings()
    logger.info("配置已重新加载")
```

**使用**：
```bash
curl -X POST http://localhost:8000/admin/config/reload
```

**注意**：
- ⚠️ 仅开发环境使用
- ⚠️ 生产环境不应暴露此接口
- ⚠️ 已有请求可能受影响

---

### 方案3：环境变量（生产环境标准）⭐⭐⭐⭐⭐

**原理**：使用容器编排平台的环境变量配置

**Docker Compose**：
```yaml
services:
  foxchatrag:
    image: foxchatrag:latest
    environment:
      - MODEL__DEFAULT_LLM=${MODEL_LLM:-ds_model}
      - MODEL__DEFAULT_JSON_LLM=${MODEL_JSON_LLM:-json_ds_model}
```

**修改后**：
```bash
# 重建容器
docker-compose up -d
```

**Kubernetes**：
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: foxchatrag-config
data:
  MODEL__DEFAULT_LLM: "minimax_model"
---
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      containers:
      - name: foxchatrag
        envFrom:
        - configMapRef:
            name: foxchatrag-config
```

修改 ConfigMap 后：
```bash
# 滚动更新
kubectl rollout restart deployment/foxchatrag
```

**优点**：
- ✅ 生产级标准
- ✅ 配置版本管理
- ✅ 易于审计
- ✅ 支持回滚

---

### 方案4：配置中心（企业级）⭐⭐⭐⭐

使用 Apollo、Nacos 等配置中心，支持：
- 实时推送更新
- 版本管理
- 灰度发布
- 权限控制

**适用场景**：
- 大规模微服务
- 需要频繁修改配置
- 需要配置审计

**引入复杂度**：
- 需要额外部署配置中心
- 增加系统依赖
- 运维成本上升

---

## 推荐选择

| 场景 | 推荐方案 | 理由 |
|------|---------|------|
| 开发环境 | 方案1（重启） | 简单，不频繁修改 |
| 小型生产 | 方案1（重启） | 改动少，接受短暂停机 |
| 中型生产 | 方案3（环境变量） | Docker/K8s 标准 |
| 大型/企业 | 方案4（配置中心） | 需要集中管理 |

## 结论

**对于你的项目**：
1. 开发阶段：重启应用即可（方案1）
2. 生产部署：使用环境变量（方案3）
3. 暂不需要配置中心（方案4）

**记住**：配置热更新是高级特性，引入复杂度。大多数项目通过重启来解决配置变更，这完全合理且可靠！
