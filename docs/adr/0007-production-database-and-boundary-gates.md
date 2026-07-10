# ADR 0007：生产数据库目标与边界 Gate

## 状态

已接受，作为 production-hardening 基线。

## 背景

SocialEase 使用 SQLite 作为本地开发持久化。Harness 已经具备 identity、privacy-aware persistence、consent protocol、intervention-plan lifecycle、product-safe trace 和 eval gate 等产品化边界。

在替换 SQLite 之前，需要明确生产数据库目标，确保 repository 改造不会削弱这些边界。

## 决策

SQLite 只作为本地开发后端。生产化目标是 PostgreSQL，并使用显式 migration 和事务化 repository 方法。

生产 adapter 必须保留：

- trace、roleplay session、worksheet、exposure plan、protocol、intervention plan、memory settings 的 owner-scoped reads/writes；
- consent approval、consumption 和 linked intervention-plan update 的事务边界；
- protocol expiration cleanup 索引；
- 默认 product-safe trace retention；
- ownership、privacy、consent replay、continuation safety 回归时 CI 失败。

## 必要事务边界

以下操作必须在生产 adapter 中作为单个数据库事务执行：

- `ProtocolService.respond(...)`：
  - protocol 从 `pending` 转到 `approved` 或 `rejected`；
  - 更新 linked intervention plan state。
- `ProtocolService.consume_for_action(...)`：
  - protocol 从 `approved` 转到 `consumed`；
  - 防止并发 double-consumption。
- Approved harness skill completion：
  - 执行 bounded action；
  - 标记 linked intervention plan action step completed；
  - 持久化 product-safe trace。
- Memory delete/export flows：
  - 所有 rows 按 authenticated owner scoped；
  - 避免删除或导出跨用户记录。

## Migration 目标

生产 migration 应为以下 JSON payload 内字段建立一等列：

- protocol lifecycle：`session_id`、`harness_action`、`request_hash`、`expires_at`、`approved_at`、`consumed_at`、`rejected_at`；
- trace safety/observability：`product_safe`、`created_at`、可选 privacy summary JSON。

## 影响

- SQLite repository 仍适合本地开发和测试；
- CI 覆盖 backend tests、eval gates、frontend typecheck/lint/build；
- PostgreSQL implementation 可在 repository interfaces 后逐步完成；
- metrics 长期应从进程内计数迁移到外部 metrics backend。

## 当前实现检查点

- Alembic 配置位于 `backend/alembic.ini` 和 `backend/migrations/`；
- migration 已创建当前产品表；
- PostgreSQL adapters 已覆盖当前主要 runtime path；
- repository factory 可按配置选择 SQLite 或 PostgreSQL；
- 后续重点是继续加强多实例 coordination、托管身份和部署级监控。
