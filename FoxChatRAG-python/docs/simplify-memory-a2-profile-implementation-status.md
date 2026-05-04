# simplify-memory-a2-profile 实施状态文档

## 核心架构变更

### 1. A2 边界成为边界唯一主来源
- **文件**: `app/schemas/a2_boundary.py`, `app/service/chat/a2_boundary_service.py`
- **职责**: 管理长期边界、高优先级禁忌和明确长期约束
- **存储**: Redis key `chat:memory:{user_id}:{llm_id}:a2_boundary`
- **更新触发**: Summary 流程周期触发，提取明确边界并持久化
- **Prompt 注入**: 边界在前，画像在后，作为硬边界主来源

### 2. user_profile 收敛为长期画像层
- **文件**: `app/service/chat/user_profile_service.py`, `app/core/prompts/user_profile_updater.md`
- **职责**: 只负责长期稳定用户画像，不再承担硬边界主职责
- **更新策略**: 只处理长期画像信息，临时情绪和明确边界不进入 profile
- **Prompt 注入**: 位于 A2 边界之后，作为画像主来源

### 3. Candidate 分流总线已退出主链路
- **移除**: `route_summary_candidates(...)` 主路径调用
- **移除**: `SummaryBatchResult` 作为核心编排对象
- **移除**: `_process_*_candidates(...)` 主路径函数（已注释）
- **保留**: memory_bank 作为历史保底沉淀机制

## 新架构流程

### Summary 主流程
```
summary 触发
  ↓
生成 summary_text
  ↓
A2 边界提取 → 写入 a2_boundary
  ↓
user_profile 更新 → 只处理长期画像
  ↓
memory_bank 保底沉淀
  ↓
memory_bank 压缩
```

### Prompt 注入流程
```
A1 静态锚点（soul / core_anchor / character_card）
  ↓
A2 边界（硬边界在前）
  ↓
user_profile 画像（长期画像在后）
  ↓
历史保底（memory_bank）
  ↓
recent message（短期即时连续性）
```

## 代码变更清单

### 新增文件
1. `app/schemas/a2_boundary.py` - A2 边界项数据结构定义
2. `app/service/chat/a2_boundary_service.py` - A2 边界提取与写回服务

### 修改文件
1. `app/service/chat/memory_summary_service.py`
   - 移除 candidate router 主路径调用
   - 注入 A2 边界更新逻辑
   - 注释掉 `_process_*_candidates` 函数

2. `app/service/chat/chat_msg_service.py`
   - 添加 `_parse_a2_boundary` 函数
   - 修改 `_parse_user_profile` 不再拼接 A2 边界
   - 修改 `_parse_all_memories` 先注入 A2 边界，再注入 user_profile

3. `app/common/constant/LLMChatConstant.py`
   - 添加 `A2_BOUNDARY` 常量

4. `app/core/prompts/user_profile_updater.md`
   - 明确只处理长期画像，不处理硬边界
   - 添加 "边界归 A2" 规则

## 待完成验证场景

1. 用户刚说"不要xxx" → 下一轮 recent message 生效 → summary 后 A2 固化
2. user_profile 不再因为显式硬边界产生重复主写
3. active A2 边界在 Prompt 中先于 user_profile 画像出现
4. summary 触发后边界可正确持久化为 active A2 条目

## 回滚方案

如果出现问题，可以通过以下方式回滚：
1. 取消 `memory_summary_service.py` 中的 A2 边界更新调用
2. 恢复 `route_summary_candidates(...)` 主路径调用
3. 取消 `chat_msg_service.py` 中的 `_parse_a2_boundary` 调用
4. 恢复 `_parse_user_profile` 拼接 A2 边界的逻辑