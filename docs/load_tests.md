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

然后运行：

```bash
cd backend
SOCIALEASE_TEST_DATABASE_URL=postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease pytest -m load
```

PostgreSQL fresh migration 测试会把配置的测试数据库降到 `base`，再升级到 `head`。不要把它指向任何需要保留数据的数据库。

## 当前范围

为了让本地测试可执行，当前并发规模较小：

- 16 个并发 protocol approvals；
- 32 个并发 protocol consumes；
- 50 个并发 chat runs。

## 最近本地结果

运行日期：2026-07-03

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

已知限制：

- 这是 in-process ASGI 回归，不是网络 benchmark；
- 不测量真实部署服务器和数据库下的 p95 latency；
- LLM provider call 仍主要通过 timeout/concurrency fallback 保持安全，不对真实 provider 做压测。

如果要做正式 benchmark，需要单独记录 throughput、latency percentiles、error rate、机器配置和数据库配置。
