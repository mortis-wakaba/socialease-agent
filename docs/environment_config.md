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
| `SOCIALEASE_AUTH_TOKEN_KEYS` | production rotation | JSON key ring，例如 `{"2026-01":"...","2026-07":"..."}`；配置后替代单一 secret |
| `SOCIALEASE_AUTH_TOKEN_ACTIVE_KID` | production rotation | 新签发 token 使用的 key id；轮换时先加入新 key，再切换 active id，最后等待旧 token 过期后删除旧 key |
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

## Calendar MCP

| 变量 | 必需场景 | 含义 |
|---|---|---|
| `SOCIALEASE_CALENDAR_MCP_URL` | remote MCP | Calendar MCP Streamable HTTP URL；为空时使用明确标注的进程内 Demo Provider |
| `SOCIALEASE_OUTBOX_INTERVAL_SECONDS` | `2` | module/Calendar reconciliation worker 轮询间隔 |
| `SOCIALEASE_OUTBOX_BATCH_SIZE` | `20` | 每次租约领取上限，最大 100 |
| `SOCIALEASE_OUTBOX_LEASE_SECONDS` | `60` | worker 崩溃后可被其他副本重新领取的租约时长 |

Calendar create/update/delete 不再在 API 请求内直接消费协议后裸调用 MCP。批准协议的消费与
`calendar_action_outbox` 入队位于同一 PostgreSQL 事务；API 会尝试一次低延迟执行，失败则由
reconciliation worker 按指数退避重放。`/ready` 的 `checks.outbox` 仅暴露 pending、
processing、dead-letter 数量和最老待处理时间，不包含用户或事件内容。
| `SOCIALEASE_CALENDAR_MCP_HOST` | local MCP server | 独立 MCP Server 监听地址，默认仅本机 `127.0.0.1` |
| `SOCIALEASE_CALENDAR_MCP_PORT` | local MCP server | 独立 MCP Server 端口，默认 `8010` |
| `SOCIALEASE_CALENDAR_MCP_SERVER_TRANSPORT` | local MCP server | `streamable-http` 或 `stdio`；默认使用官方推荐的 Streamable HTTP |

本地真实 MCP 协议演示需要分别启动 Calendar MCP Server 和 Backend：

```bash
make dev-calendar-mcp
SOCIALEASE_CALENDAR_MCP_URL=http://127.0.0.1:8010/mcp make dev-backend
```

当前 MCP Server 使用内存 Demo Provider，用于协议、Consent、owner scope、幂等和回读验证，
不会写入 Google 或 Microsoft 日历。真实厂商接入需要实现新的 `CalendarProvider` 和用户级
OAuth Token 管理；不能把共享 Token、用户日程正文或凭据写入 Trace。

## Database

| 变量 | 必需场景 | 含义 |
|---|---|---|
| `SOCIALEASE_DATABASE_URL` | staging/production target | 数据库 URL；为空时使用本地 SQLite 默认值 |
| `SOCIALEASE_DB_PATH` | local/Docker | SQLite 数据库路径覆盖 |
| `SOCIALEASE_SQLITE_TIMEOUT_SECONDS` | local | SQLite busy timeout |
| `SOCIALEASE_CONVERSATION_CONTENT_KEY` | production auth | 32 字节随机密钥的 URL-safe Base64；用于 Conversation 正文 AES-256-GCM，加密模式缺少或格式错误时拒绝初始化 Conversation 持久化 |
| `SOCIALEASE_CONVERSATION_CONTENT_KEY_VERSION` | production auth | 非秘密的密钥版本标签；用于识别密文所需密钥，轮换前必须先设计旧密文重加密流程 |
| `SOCIALEASE_TEST_DATABASE_URL` | integration tests | 集成测试使用的 PostgreSQL URL |
| `SOCIALEASE_BACKUP_DIR` | staging/production | `scripts/backup_database.sh` 的备份输出目录 |

SQLite 是默认本地开发运行时，demo auth 未配置内容密钥时会明确使用本地明文保护器。
Production auth 不允许这一降级。PostgreSQL repository adapters 已覆盖当前主要运行路径，
但真实试点前仍需在 secret manager 中生成和保管内容密钥，并完成托管备份、恢复演练、
密钥轮换方案和 migration 检查。不要在已有密文仍需读取时直接替换或删除旧密钥。

## LLM Provider

| 变量 | 必需场景 | 含义 |
|---|---|---|
| `SOCIALEASE_APP_VERSION` | deployment | 写入 Product/Eval Trace 的非敏感应用版本，例如 Git commit 或 release tag |
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
| `SOCIALEASE_REDIS_URL` | context cache / multi-instance | 统一 Conversation Context、Module Overlay、Worksheet Draft 和 Resource 引用状态共用的 Redis URL |
| `SOCIALEASE_REQUIRE_REDIS` | production | production 默认 `true`；缺少 Redis URL 时拒绝启动，Redis 探针失败时 `/ready` 返回 503 |
| `SOCIALEASE_DOCKER_REDIS_URL` | local compose | 可选的 backend 容器 Redis URL；默认 `redis://redis:6379/0`，避免把宿主机的 `localhost` 地址传入容器 |
| `SOCIALEASE_REDIS_SOCKET_TIMEOUT_SECONDS` | Redis | 所有 Redis projection/task state 共用的有界连接超时，默认 0.5 秒 |
| `CONVERSATION_CONTEXT_CACHE_TTL_SECONDS` | conversation | 加密 Working Context Projection Cache TTL，默认 3600 秒 |
| `MODULE_OVERLAY_CACHE_TTL_SECONDS` | modules | 加密 Module Overlay Cache TTL，默认 3600 秒 |
| `WORKSHEET_DRAFT_TTL_SECONDS` | worksheet | Worksheet 当前字段引用和草稿状态 TTL，默认 3600 秒；不复制统一 Timeline 原文 |
| `SUPPORT_SEARCH_TTL_SECONDS` | resource | Resource Citation ID 指代状态 TTL，默认 1800 秒；不缓存任意网页正文 |
| `SOCIALEASE_RATE_LIMIT_BACKEND` | pilot | `local` 进程内限流；`gateway` 表示网关限流；`redis` 目前 fail fast |
| `SOCIALEASE_LLM_CONCURRENCY_BACKEND` | future multi-instance | `local` 进程内 semaphore；`redis` 为未来共享 provider semaphore |

Redis 保存加密、版本化的统一 Conversation Context Projection 和 Module Overlay，以及
Worksheet 当前字段引用、Resource Citation ID 等允许过期的任务状态。Role-play 消息不再
拥有独立 Redis window，正文只来自 Conversation Timeline。Redis miss/timeout 会从数据库
重建，不能改变用户历史或冒充数据库事实。production 模式默认要求 Redis：未配置 URL 时
拒绝启动，探针失败时 `/ready` 返回 503；请求正确性仍不以缓存命中为前提。当前限流仍是
进程内实现，多实例试点时只有部署网关真正执行请求预算，才设置
`SOCIALEASE_RATE_LIMIT_BACKEND=gateway`。

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

Conversation History 在用户确认版本化持久化告知后默认 `expires_at = NULL`，不受上述
Trace/Protocol cleanup window 影响，直到用户主动删除。真实用户部署前，retention 设置
必须和隐私说明、试点同意材料保持一致。
