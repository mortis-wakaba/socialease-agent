# SocialEase 数据库迁移规范

项目使用 Alembic 管理 PostgreSQL schema。SQLite 仍用于本地开发路径，但任何影响 PostgreSQL repository 的 schema 变化都必须对应 Alembic revision。

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
