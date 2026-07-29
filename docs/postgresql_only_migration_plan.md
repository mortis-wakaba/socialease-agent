# PostgreSQL-only 迁移实施计划

> 状态：已完成
> 创建日期：2026-07-29
> 范围：Backend 权威持久化、统一会话、长期记忆、测试与本地 Demo
> 非目标：修改 Agent Prompt、启用 pgvector、改变 Memory Consent 或安全策略

## 1. 目标状态

```text
Service / Workflow
        |
        v
Repository Protocol + Pydantic Domain Model
        |
        v
PostgreSQL Adapter
        |
        v
PostgreSQL（唯一持久化事实源）
```

- Service、Workflow 和 API 只依赖 Repository Protocol，不拼接 SQL。
- PostgreSQL SQL、事务、行锁、advisory lock 和 JSONB 映射只存在于
  `app/db/postgres/` adapter 或数据库运维模块。
- SQLite 不再是运行时 provider，也不再有完整 Repository 实现。
- 单元测试使用不持久化的 fake；Repository 契约测试在隔离 PostgreSQL 数据库运行。
- 不支持的 provider 在配置解析阶段明确失败，不允许回退到 SQLite。

## 2. 阶段与验收

### 阶段 0：基线和依赖清单

- 固定当前工作树，保留尚未提交的 Memory Scale Eval。
- 枚举 SQLite Repository、直接 SQL、测试和文档引用。
- 区分领域 Protocol、共享映射工具、PostgreSQL adapter 与待删除实现。

验收：

- 依赖清单覆盖运行时、测试、CI、Docker、脚本和文档。
- 删除范围不包含 Pydantic model、Repository Protocol、Alembic 历史 migration。

### 阶段 1：PostgreSQL-only 配置和能力门禁

- `SOCIALEASE_DATABASE_URL` 必须是 PostgreSQL URL，不再生成 SQLite 默认值。
- `DatabaseProvider` 和 `DatabaseCapabilityReport` 只声明 PostgreSQL 能力。
- Capability Report 显式列出所有完整运行时 Repository，包括 Conversation、
  Long-term Memory、Memory Proposal、Calendar Outbox 和 deletion/export 边界。
- 未配置、SQLite URL 或未知 provider 在启动/Factory 创建前明确失败。
- Docker、CI、README 和环境示例默认使用 PostgreSQL。

验收：

- 缺失 URL、SQLite URL、未知 URL 均有确定性失败测试。
- PostgreSQL capability report 无 missing repository。
- App 在 PostgreSQL 配置下导入时不触碰 SQLite。

### 阶段 2：Factory 和运行路径收敛

- `RepositoryFactory` 只创建 PostgreSQL adapter。
- 删除各运行时模块中的 SQLite fallback 和进程级 SQLite store。
- Service 构造函数继续接收 Protocol，测试可以注入 fake。
- 将 Service 中残留的持久化 SQL 下沉到 PostgreSQL adapter。

验收：

- `backend/app/services`、`api`、`workflow` 不包含业务 SQL。
- Factory 的每个方法只返回对应 PostgreSQL adapter。
- 没有 provider 分支或静默降级。

### 阶段 3：删除 SQLite 完整实现

- 删除统一会话、长期 Memory、Proposal、设置、账户、Trace、Role-play、
  Worksheet、Exposure、Profile、Review、Protocol、Plan、Metrics 的 SQLite 实现。
- 保留 Protocol、领域异常、共享纯函数、Pydantic model。
- PostgreSQL adapter 不再从 SQLite 实现模块导入数据库相关 helper；共享纯函数移至
  明确的 contract/codec 模块。
- 删除 SQLite schema initializer、连接 helper 和运行时 `sqlite3` 依赖。
- Alembic 历史 migration 保留，不重写已发布 revision。

验收：

- `backend/app` 中不存在 `SQLite*Repository`。
- `backend/app` 中不存在运行时 `sqlite3.connect`。
- PostgreSQL adapter 可以独立导入。

### 阶段 4：数据库无关契约测试

- 为 Repository Protocol 建立可复用 contract suite。
- Contract suite 不引用具体 adapter 类型或数据库 SQL。
- PostgreSQL fixture 负责 migration、隔离、清理和 adapter 构造。
- Service/Workflow 单元测试使用显式 fake，不使用 SQLite 伪装生产事务。
- 并发、锁、幂等、级联删除和 outbox 测试只在真实 PostgreSQL 执行。

验收：

- 同一 contract suite 可由任意未来 adapter 实现复用。
- CI 普通 Backend 测试与 PostgreSQL integration 不再形成 SQLite/PostgreSQL
  两套不同事实。
- 未配置隔离测试数据库时明确 skip 或失败，不连接开发/生产数据库。

### 阶段 5：本地 Demo、运维和文档

- `docker compose up` 默认启动 PostgreSQL、Redis、Backend、Frontend。
- 移除 SQLite volume、`SOCIALEASE_DB_PATH` 和 SQLite backup/restore 分支。
- 本地 Demo、E2E、smoke、backup、restore、readiness 全部使用 PostgreSQL。
- 文档明确 PostgreSQL-only；未来 SQLite Demo 只能作为显式、能力受限的新 adapter
  重新引入，不能静默兼容。

验收：

- Docker Compose 配置通过。
- Migration、readiness、backup/restore dry-run 和 smoke 配置检查通过。
- Repository privacy、Prompt version、Eval gate 和前后端回归全部通过。

## 3. 固定验证顺序

每阶段至少执行：

```bash
git diff --check
cd backend
pytest -q <本阶段测试>
```

最终执行：

```bash
make migration-check
make privacy-check
make prompt-version-check
make eval
make eval-gate
make test-postgres-runtime
make typecheck-frontend
make lint-frontend
make build-frontend
```

## 4. 回滚与兼容边界

- 不修改已发布 Alembic migration；只删除 SQLite 运行时代码。
- PostgreSQL 数据和 schema 不因本迁移变化。
- 保留 Repository Protocol 后，未来可以新增能力受限的 SQLite Demo adapter。
- 未来 adapter 必须显式声明 capability，并通过共享 contract suite；不得成为生产
  fallback。

## 5. 实施结果（2026-07-29）

- 运行时持久化已收敛为 PostgreSQL-only；SQLite Repository、连接初始化和
  backup/restore 分支已删除。
- Conversation 与 Long-term Memory 契约测试只引用 Repository Protocol，具体
  PostgreSQL 绑定由测试 fixture 提供。
- `DatabaseCapabilityReport` 同时公开可用与不可用能力；当前明确报告
  `vector_similarity_search` 不可用，不做静默回退。
- 扩展后的 Memory Eval 包含 59 个查询、2,135 条索引记忆和单查询最多 2,053 个候选。
  Vector/Hybrid 显示召回潜力，但 `vector_gate_met=false`，因此本阶段不启用 pgvector。
- 最终后端回归：531 passed、6 skipped；Eval 与 Eval Gate 全部通过。
