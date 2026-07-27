# SocialEase 负载回归测试

这组测试是面向回归的并发测试，不是正式性能 benchmark。

它检查 SocialEase 在中等竞争条件下是否仍然稳定：

- 并发 consent approval；
- 并发 protocol consume；
- 多用户并发 chat run；
- active sessions 和 cleanup 同时发生时的 memory export/delete；
- 配置测试数据库时，从空 PostgreSQL migration 到 head；
- 50 个不同用户的 deterministic chat 并发请求。

## 运行本地负载回归

```bash
cd backend
pytest -m load
```

## 带 PostgreSQL Migration 覆盖运行

先启动本地数据库：

```bash
docker compose up -d postgres
```

然后在仓库根目录创建独立测试库（只需一次），再进入后端运行：

```bash
docker compose exec postgres createdb -U socialease socialease_test  # 仅首次创建
cd backend
SOCIALEASE_TEST_DATABASE_URL=postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease_test pytest -m load
```

PostgreSQL fresh migration 测试会把配置的测试数据库降到 `base`，再升级到 `head`。必须使用可丢弃的独立测试库，绝不能指向开发库、试点库或任何需要保留数据的数据库。完整 PostgreSQL Repository/Runtime 检查优先运行仓库目标：

```bash
SOCIALEASE_TEST_DATABASE_URL=postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease_test \
  make test-postgres-runtime
```

## 当前范围

为了让本地测试可执行，当前并发规模较小：

- 16 个并发 protocol approvals；
- 32 个并发 protocol consumes；
- 50 个并发 chat runs。

## 最近本地结果

负载测试记录日期：2026-07-27（统一上下文完成基线）

命令：

```bash
cd backend
pytest -m load
```

结果：

| 检查项 | 本地结果 |
|---|---:|
| Concurrent protocol approval | passed |
| Concurrent protocol consume | passed |
| 50 concurrent chat runs | passed |
| Memory export/delete with cleanup | passed |
| Fresh PostgreSQL migration | 未设置 `SOCIALEASE_TEST_DATABASE_URL` 时跳过 |

本地汇总：`4 passed, 3 skipped`；跳过项需要独立 PostgreSQL/Redis 服务。统一 Context
Cache 的 hit、miss、并发 single-flight、Redis timeout 后 DB rebuild 分别由
`test_conversation_context_cache.py` 验证；真实 Redis 与 PostgreSQL 延迟仍以 CI 服务
容器或部署环境为准，不把内存 fake 的耗时当成生产 benchmark。

2026-07-20 的完整外部状态集成基线为：PostgreSQL `29 passed`，Redis `2 passed`。这些数字是 Repository/Runtime 集成结果，不是正式吞吐量 benchmark。

已知限制：

- 这是 in-process ASGI 回归，不是网络 benchmark；
- 不测量真实部署服务器和数据库下的 p95 latency；
- LLM provider call 仍主要通过 timeout/concurrency fallback 保持安全，不对真实 provider 做压测。

如果要做正式 benchmark，需要单独记录 throughput、latency percentiles、error rate、机器配置和数据库配置。
