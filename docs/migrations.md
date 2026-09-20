# SocialEase 数据库迁移规范

项目仅使用 PostgreSQL 持久化，并通过 Alembic 管理 schema。任何影响 repository 的 schema 变化都必须对应 Alembic revision。

## 命名规范

Migration 文件必须使用：

```text
NNNN_short_snake_case_description.py
```

示例：

```text
0001_initial_product_tables.py
0002_add_protocol_audit_columns.py
0003_index_trace_created_at.py
```

规则：

- 使用四位数字前缀；
- 描述保持 lowercase snake_case；
- 不复用 numeric prefix；
- 每个 revision 只包含一个逻辑 schema 变化；
- 不要在 migration 中放演示 seed data、真实学校信息或真实联系方式。

## 创建 Revision

在 backend 目录运行：

```bash
cd backend
alembic revision -m "add protocol audit columns" --rev-id 0002_add_protocol_audit_columns
```

编辑生成文件前：

- 确认 `upgrade()` 和 `downgrade()` 都是有意设计；
- 只有领域模型仍在演进时才保留 JSON payload columns；
- 如果字段会被查询、索引或参与授权边界，应拆成显式列。

## 本地验证

名称和 revision graph 检查：

```bash
cd backend
python -m app.db.migration_check --check-names-only
```

真实 PostgreSQL migration 检查：

```bash
cd backend
SOCIALEASE_DATABASE_URL=postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease_test python -m app.db.migration_check
```

本地验证应使用可丢弃的独立测试库。不要把测试或 downgrade/upgrade 演练指向开发、试点或生产数据。

## CI 期望

CI 应在 backend tests 前，对临时 PostgreSQL service 运行 migration。这样可以在合并前发现 DDL 错误、依赖缺失和 revision graph 问题。

Live migration check 只证明 schema 可以升级到 head。当前独立 PostgreSQL Runtime 测试已覆盖主要 Repository、事务边界、ownership 和 fresh-process 持久化；真实部署仍需在目标基础设施上验证连接池配置、备份恢复与 migration/rollback 流程。

当前 head 是 `0026_command_safety_recovery`。`0012` 之后的统一会话、异步副作用与
Memory 检索 schema 分布在：

- `0012_unified_conversations`：Conversation、Event、Module Proposal/Run；
- `0013_conversation_context`：有界上下文 Compact Summary；
- `0014_conversation_deletion_receipts`：幂等删除回执与级联删除支持；
- `0015_conversation_command_inbox`：状态写命令幂等与结果重放；
- `0016_module_start_outbox`：模块启动事务 outbox；
- `0017_module_outbox_leases`：worker 租约与失败恢复；
- `0018_calendar_action_outbox`：Calendar 外部副作用 outbox；
- `0019_calendar_demo_idempotency`：Demo Calendar Provider 持久化幂等绑定；
- `0020_add_memory_fts_index`：Memory PostgreSQL FTS；
- `0021_add_memory_vector_index`：pgvector、版本化 512 维 embedding 与 HNSW index。
- `0022_conversation_intent_plans`：会话级结构化意图计划；
- `0023_intent_effect_ledger`：意图副作用 ledger；
- `0024_conversation_command_leases`：多实例命令租约、接管和写入 fencing；
- `0025_command_abandonment`：过期命令显式 abandoned 生命周期与刷新恢复处置；
- `0026_command_safety_recovery`：独立持久化命令执行与危机安全恢复状态，未完成安全恢复时禁止 abandon 和普通执行。

不要依赖这里的编号判断部署状态；发布时仍必须以 `alembic heads` 和
`python -m app.db.migration_check` 的实际结果为准。
