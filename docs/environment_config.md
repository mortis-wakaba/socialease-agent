# SocialEase 环境变量配置

本文说明本地开发、受控试点和未来部署会用到的环境变量。真实 secret 必须放在本地忽略的 env 文件或部署平台 secret manager 中，不能提交到 Git。

## 示例文件

| 文件 | 用途 |
|---|---|
| `.env.example` | 本地开发和 Docker Compose 默认值 |
| `.env.staging.example` | staging 或小规模校园试点检查表 |
| `.env.production.example` | 未来部署检查表，不代表当前已完成正式生产环境 |

## 模式矩阵

| 运行环境 | 后端模式 | 前端模式 | 用途 |
|---|---|---|---|
| 本地开发 | `SOCIALEASE_AUTH_MODE=demo` | `NEXT_PUBLIC_SOCIALEASE_AUTH_MODE=demo` | 用 `X-Demo-User-Id` 和前端 Demo User 控件快速展示 |
| Staging / pilot | `SOCIALEASE_AUTH_MODE=production` | `NEXT_PUBLIC_SOCIALEASE_AUTH_MODE=production` | 基于账号的受控用户测试，启用前端 route guard 和后端 auth |
| 未来生产目标 | `SOCIALEASE_AUTH_MODE=production` | `NEXT_PUBLIC_SOCIALEASE_AUTH_MODE=production` | 关闭公开注册、cookie、限流、备份和监控等真实部署检查 |

试点环境不要混用 demo 前端和 production 后端，否则 UI 仍会展示 demo 身份控件，而后端会拒绝 demo header。展示环境也不要随意混用 production 前端和 demo 后端，除非你明确需要登录保护页面。

## Backend / Frontend URLs

| 变量 | 必需场景 | 含义 |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | frontend | Next.js 浏览器侧访问的 FastAPI base URL |
| `NEXT_PUBLIC_SOCIALEASE_AUTH_MODE` | frontend | `demo` 显示本地演示控件，`production` 隐藏 demo 身份控件 |
| `NEXT_PUBLIC_SOCIALEASE_ENABLE_SIGNUP` | frontend | `false` 在 pilot/production 登录页隐藏公开注册 |
| `NEXT_PUBLIC_SOCIALEASE_TOKEN_STORAGE` | frontend | `cookie` 避免 JS 直接读取 token；`localStorage` 仅用于本地兼容 |
| `SOCIALEASE_CORS_ORIGINS` | backend | 允许访问后端的前端 origin 列表，逗号分隔 |

## Authentication

| 变量 | 必需场景 | 含义 |
|---|---|---|
| `SOCIALEASE_AUTH_MODE` | backend | `demo` 用于本地开发，`production` 启用认证模式 |
| `SOCIALEASE_AUTH_TOKEN_SECRET` | production | 当前自建 HS256 JWT/session 的签名 secret |
| `SOCIALEASE_ENABLE_SIGNUP` | staging/production | 后端强制的公开注册开关；production auth 下未设置时默认关闭 |
| `SOCIALEASE_SIGNUP_ALLOWED_EMAILS` | staging/pilot | 关闭公开注册时允许注册的邮箱 allowlist |
| `SOCIALEASE_SIGNUP_INVITE_CODES` | staging/pilot | 关闭公开注册时 `/api/auth/register` 接受的邀请码 |
| `SOCIALEASE_AUTH_COOKIE_ENABLED` | staging/pilot | register/login/refresh 时设置 HttpOnly auth cookie |
| `SOCIALEASE_AUTH_COOKIE_SECURE` | staging/production | 为 auth cookie 设置 Secure，HTTPS 后应为 `true` |

Production cookie 模式还会设置非 HttpOnly 的 `socialease_csrf_token`，用于 double-submit CSRF 防护。Cookie 认证的写请求需要通过 Origin/Referer allowlist 或携带 `X-CSRF-Token`。

当前 production auth 仍是项目内自建实现。真实试点前建议迁移到 OIDC 或托管身份服务。

## Consent And Safety Controls

| 变量 | 必需场景 | 含义 |
|---|---|---|
| `SOCIALEASE_ENFORCE_DIRECT_ACTION_CONSENT` | staging/production | 强制直接写状态的练习 API 走 consent protocol |

## Database

| 变量 | 必需场景 | 含义 |
|---|---|---|
| `SOCIALEASE_DATABASE_URL` | staging/production target | 数据库 URL；为空时使用本地 SQLite 默认值 |
| `SOCIALEASE_DB_PATH` | local/Docker | SQLite 数据库路径覆盖 |
| `SOCIALEASE_SQLITE_TIMEOUT_SECONDS` | local | SQLite busy timeout |
| `SOCIALEASE_TEST_DATABASE_URL` | integration tests | 集成测试使用的 PostgreSQL URL |
| `SOCIALEASE_BACKUP_DIR` | staging/production | `scripts/backup_database.sh` 的备份输出目录 |

SQLite 是默认本地开发运行时。PostgreSQL repository adapters 已覆盖当前主要运行路径，但真实试点前仍需托管备份、恢复演练和 migration 检查。

## LLM Provider

| 变量 | 必需场景 | 含义 |
|---|---|---|
| `LLM_ENABLED` | optional | `true` 启用 provider call；`false` 使用 deterministic fallback |
| `LLM_PROVIDER` | optional | 当前值：`openai_compatible` |
| `LLM_BASE_URL` | optional | Provider base URL，例如 DeepSeek |
| `LLM_API_KEY` | LLM enabled | Provider API key |
| `LLM_MODEL` | optional | 模型名称 |
| `LLM_TIMEOUT_SECONDS` | optional | Provider timeout |
| `LLM_MAX_CONCURRENCY` | pilot | 进程内全局 LLM 并发上限；`0` 表示禁用 |
| `LLM_CONCURRENCY_WAIT_SECONDS` | pilot | 等待 LLM capacity 的时间，超时后 fallback |

Provider 失败或关闭时，系统仍必须保持安全。

## Rate Limit And Reliability

| 变量 | 必需场景 | 含义 |
|---|---|---|
| `SOCIALEASE_RATE_LIMIT_PER_MINUTE` | pilot | 单用户 API sliding-window limit；`0` 关闭本地限制 |
| `SOCIALEASE_AUTH_RATE_LIMIT_PER_MINUTE` | pilot | 登录/注册 sliding-window limit；production auth 下未设置时默认保守非零 |
| `LLM_MAX_CONCURRENCY` | pilot | 推荐的全局 LLM 并发上限变量 |
| `SOCIALEASE_LLM_MAX_CONCURRENCY` | legacy | `LLM_MAX_CONCURRENCY` 的兼容别名 |
| `SOCIALEASE_SLOW_REQUEST_MS` | pilot | structured log 和 slow-request metrics 阈值 |
| `SOCIALEASE_REDIS_URL` | future multi-instance | 未来共享限流或并发协调使用的 Redis URL |
| `SOCIALEASE_RATE_LIMIT_BACKEND` | pilot | `local` 进程内限流；`gateway` 表示网关限流；`redis` 目前 fail fast |
| `SOCIALEASE_LLM_CONCURRENCY_BACKEND` | future multi-instance | `local` 进程内 semaphore；`redis` 为未来共享 provider semaphore |

当前本地运行时限制是进程内的。多实例试点时，只有部署网关真正执行请求预算，才设置 `SOCIALEASE_RATE_LIMIT_BACKEND=gateway`。`redis` 当前为预留值，在共享 adapter 完成前会 fail fast。

## Operational Alerts

| 变量 | 必需场景 | 含义 |
|---|---|---|
| `SOCIALEASE_MONITOR_BASE_URL` | alert job | `scripts/monitor_alerts.py` 检查的后端 base URL |
| `SOCIALEASE_ALERT_WEBHOOK_URL` | alert job | 告警 payload 的通用 JSON webhook |
| `SOCIALEASE_ALERT_CRISIS_RUNS` | alert job | crisis run 数超过阈值时告警 |
| `SOCIALEASE_ALERT_FALLBACK_RUNS` | alert job | fallback run 数超过阈值时告警 |
| `SOCIALEASE_ALERT_RATE_LIMIT_HITS` | alert job | rate-limit hit 超过阈值时告警 |
| `SOCIALEASE_ALERT_LLM_CONCURRENCY_SATURATION` | alert job | LLM concurrency saturation 超过阈值时告警 |
| `SOCIALEASE_ALERT_SLOW_REQUESTS` | alert job | slow request 超过阈值时告警 |
| `SOCIALEASE_ALERT_LATENCY_P95_MS` | alert job | p95 latency 超过阈值时告警 |

阈值设为 `-1` 表示关闭对应告警。告警脚本只读取 readiness 和 aggregate metrics，不导出用户原文或 trace ID。

## Privacy And Retention

| 变量 | 必需场景 | 含义 |
|---|---|---|
| `SOCIALEASE_TRACE_RETENTION_DAYS` | pilot | cleanup 时删除超过该天数的 trace rows |
| `SOCIALEASE_PROTOCOL_RETENTION_DAYS` | pilot | 删除超过该天数的 terminal protocol/intervention-plan rows |
| `SOCIALEASE_ABANDONED_PLAN_MINUTES` | cleanup job | pending-consent plan 自动取消窗口 |

真实用户部署前，retention 设置必须和隐私说明、试点同意材料保持一致。
