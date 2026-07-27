# SocialEase 部署与运维 Runbook

SocialEase 是产品化 Agent 原型，不是医疗产品，也不是正式危机服务。任何真实用户试点都必须先完成机构、隐私、安全和运维审核。

## 部署形态

低成本试点形态：

- Frontend：Vercel 或 Netlify；
- Backend：Render、Fly.io、Railway 或小型 Docker host；
- Database：managed PostgreSQL；
- Secrets：部署平台 secret manager；
- HTTPS：平台托管 TLS 或 Nginx reverse proxy。

单机可控形态：

- Docker Compose；
- Nginx reverse proxy；
- PostgreSQL volume 或 managed PostgreSQL；
- `scripts/backup_database.sh` + cron；
- 可选 Prometheus/Grafana 或云平台 metrics。

仓库提供单机 production template：

```bash
cp .env.production.example .env.production
# 编辑所有 placeholder，并让 SOCIALEASE_TLS_CERT_DIR 指向证书目录
docker compose -f docker-compose.prod.yml --env-file .env.production config
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

## 必要环境变量

Backend：

```bash
SOCIALEASE_DATABASE_URL=postgresql+psycopg://...
SOCIALEASE_CORS_ORIGINS=https://your-frontend.example.edu
SOCIALEASE_AUTH_MODE=production
SOCIALEASE_AUTH_TOKEN_SECRET=...
SOCIALEASE_CONVERSATION_CONTENT_KEY=...
SOCIALEASE_CONVERSATION_CONTENT_KEY_VERSION=v1
SOCIALEASE_REDIS_URL=rediss://...
SOCIALEASE_REQUIRE_REDIS=true
LLM_ENABLED=false
```

Frontend：

```bash
NEXT_PUBLIC_API_BASE_URL=https://your-api.example.edu
NEXT_PUBLIC_SOCIALEASE_AUTH_MODE=production
NEXT_PUBLIC_SOCIALEASE_ENABLE_SIGNUP=false
NEXT_PUBLIC_SOCIALEASE_TOKEN_STORAGE=cookie
NEXT_PUBLIC_SOCIALEASE_SHOW_TRACE=false
NEXT_PUBLIC_SOCIALEASE_SHOW_DIAGNOSTICS=false
```

`SOCIALEASE_CONVERSATION_CONTENT_KEY` 必须是 URL-safe Base64 编码的 32 字节随机密钥。
它和认证密钥、数据库凭据一样必须保存在 secret manager，不能提交到 Git；已有密文仍
需读取时不能直接替换或丢弃旧密钥。当前 `v1` 标签不等于自动轮换机制，轮换前必须先
完成旧密文重加密方案。

所有真实 secret 必须留在 Git 外部。
修改 `NEXT_PUBLIC_*` 后需要重新 build frontend image，不能只重启容器。

`docker-compose.prod.yml` 需要的 TLS 文件：

```text
${SOCIALEASE_TLS_CERT_DIR}/fullchain.pem
${SOCIALEASE_TLS_CERT_DIR}/privkey.pem
```

## Health 与 Readiness

Liveness：

```bash
curl -fsS https://your-api.example.edu/health
```

Readiness：

```bash
curl -fsS https://your-api.example.edu/ready
```

`/ready` 检查：

- 数据库能执行 `SELECT 1`；
- repository capability matrix 支持当前 runtime；
- Alembic migration graph 有效；
- 统一 Conversation Context、Module Overlay 和短期任务状态的 Redis 后端可用；
- aggregate metrics backend 能返回 snapshot。

Readiness response 不返回数据库 URL 或 secret。

## Metrics 与告警

聚合运行指标：

```bash
curl -fsS "https://your-api.example.edu/api/harness/metrics?limit=100"
```

建议告警：

- `/ready` 连续失败；
- crisis count 高于试点基线；
- fallback count 高于试点基线；
- rate-limit hit 异常升高；
- LLM concurrency saturation 持续非零；
- p95 latency 超过阈值；
- API 5xx rate 超过阈值。

不要把用户原文、assistant 原文、user ID、run ID 或 session ID 导出到外部 metrics。

内置告警检查：

```bash
SOCIALEASE_MONITOR_BASE_URL=https://your-api.example.edu \
python scripts/monitor_alerts.py --dry-run
```

发送到 JSON webhook：

```bash
SOCIALEASE_MONITOR_BASE_URL=https://your-api.example.edu \
SOCIALEASE_ALERT_WEBHOOK_URL=https://alerts.example.edu/socialease \
python scripts/monitor_alerts.py
```

Cron 示例：

```bash
cp scripts/socialease-alerts.cron.example /tmp/socialease-alerts.cron
# 编辑 URL 和 webhook
crontab /tmp/socialease-alerts.cron
```

## Migration 流程

部署前：

```bash
cd backend
python -m app.db.migration_check --check-names-only
SOCIALEASE_DATABASE_URL=postgresql+psycopg://... python -m app.db.migration_check
```

推荐顺序：

1. 备份数据库；
2. 在 staging 跑 migration check；
3. 在 staging/test restore database 做 restore drill；
4. 部署 backend；
5. 检查 `/ready`；
6. 部署 frontend；
7. 跑 smoke check。

## 备份

SQLite 本地开发库：

```bash
SOCIALEASE_DB_PATH=backend/socialease.db bash scripts/backup_database.sh
```

PostgreSQL：

```bash
SOCIALEASE_DATABASE_URL=postgresql+psycopg://... \
SOCIALEASE_BACKUP_DIR=/secure/backups \
bash scripts/backup_database.sh
```

备份应通过 cron 或部署平台 scheduler 执行，并存放在私有 bucket 或加密卷中，开启访问日志。

Cron 示例：

```bash
cp scripts/socialease-backup.cron.example /tmp/socialease-backup.cron
# 编辑 database URL 和路径
crontab /tmp/socialease-backup.cron
```

## 恢复

SQLite：

```bash
SOCIALEASE_DB_PATH=backend/socialease.db \
bash scripts/restore_database.sh backups/socialease-sqlite-YYYYMMDDTHHMMSSZ.db
```

PostgreSQL：

```bash
SOCIALEASE_DATABASE_URL=postgresql+psycopg://... \
bash scripts/restore_database.sh backups/socialease-postgres-YYYYMMDDTHHMMSSZ.dump
```

真实试点前必须在 staging 做恢复演练。

Restore drill：

```bash
# SQLite local backup
bash scripts/restore_drill.sh backups/socialease-sqlite-YYYYMMDDTHHMMSSZ.db

# PostgreSQL backup 恢复到专用演练数据库，不要指向生产库
SOCIALEASE_RESTORE_TEST_DATABASE_URL=postgresql+psycopg://... \
bash scripts/restore_drill.sh backups/socialease-postgres-YYYYMMDDTHHMMSSZ.dump
```

## Rollback Plan

应用回滚：

1. 保留上一版可用 backend 和 frontend image/build；
2. 如果部署后 `/ready` 失败，优先回滚 backend；
3. 如果 backend ready 但 frontend 出错，只回滚 frontend；
4. 回滚后重新跑 E2E smoke。

数据库回滚：

1. 非破坏性 migration 优先 forward fix；
2. 破坏性或高风险 migration 部署前必须立即备份；
3. 只有确认数据损失影响并停止写入后，才从备份恢复。

## E2E Smoke Test

```bash
cd frontend
npx playwright install chromium
npm run test:e2e
```

当前 E2E 套件主要使用 mocked backend 响应做前端产品流回归。部署环境 smoke 可复用同样流程，但需要 staging API、测试账号和 seed data。

部署 smoke check：

```bash
SOCIALEASE_MONITOR_BASE_URL=https://your-api.example.edu \
SOCIALEASE_SMOKE_FRONTEND_URL=https://your-frontend.example.edu \
python scripts/deployment_smoke_check.py
```

该脚本检查 `/health`、`/ready`、aggregate metrics 和前端页面。

## 多实例注意事项

当前本地 runtime 的 rate limit 和 LLM concurrency limit 是进程内的。多实例部署前需要选择以下方式之一：

- 在 API gateway 或 load balancer 执行 rate limit；
- 使用 Redis/Upstash 存储共享 sliding-window request buckets；
- 使用 Redis 实现共享 LLM provider semaphore；
- 将 `/api/harness/metrics` 保持在 PostgreSQL backend，或导出到 Prometheus/OpenTelemetry。

环境标记示例：

```bash
SOCIALEASE_REDIS_URL=rediss://...
SOCIALEASE_RATE_LIMIT_BACKEND=gateway
SOCIALEASE_LLM_CONCURRENCY_BACKEND=local
```

这些标记用于说明部署策略。当前代码在没有外部 gateway 或 Redis adapter 时仍使用本地 limiter。
