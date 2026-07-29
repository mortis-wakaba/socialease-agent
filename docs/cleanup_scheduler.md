# SocialEase 清理调度器

SocialEase 的清理任务设计为运行在 FastAPI 请求进程之外。API 不应该是 retention job 的唯一执行者。

## 功能

调度器会按固定间隔调用 `RetentionService.run_once`。

当前清理动作：

- 过期 pending consent protocols；
- 取消长时间停留在 `pending_consent` 的 intervention plans；
- 删除超过 retention window 的 trace rows；
- 删除 terminal protocol / intervention-plan rows。

调度器只记录聚合计数。不能记录用户消息、assistant 回复、`user_id`、`run_id`、protocol payload 或 intervention-plan payload。

每轮执行前会尝试获取 PostgreSQL session-level advisory lock。多个 Scheduler
副本同时触发时只有一个执行清理，其余副本记录 `lock_held` 后跳过。

## 单次运行

```bash
cd backend
python -m app.jobs.cleanup_scheduler --run-once
```

## 持续运行

```bash
cd backend
python -m app.jobs.cleanup_scheduler --interval-seconds 900
```

本地验证且不改数据：

```bash
cd backend
python -m app.jobs.cleanup_scheduler --run-once --dry-run
```

## 环境变量

```text
SOCIALEASE_CLEANUP_INTERVAL_SECONDS=900
SOCIALEASE_ABANDONED_PLAN_MINUTES=60
SOCIALEASE_TRACE_RETENTION_DAYS=30
SOCIALEASE_CLEANUP_DRY_RUN=false
```

## 部署方式

推荐：

- 作为独立 worker process 运行；
- 使用 cron 或托管 scheduler 调用 `--run-once`；
- 可以部署多个触发器，但 PostgreSQL advisory lock 只允许一轮实际清理；
- 如果后续引入队列，可在独立 worker 中使用 Celery beat 或 APScheduler。

避免：

- 只依赖 FastAPI startup/background task；
- 记录原始 trace 或用户派生 payload；
- 在备份/恢复预期明确前随意删除 trace 数据。

## Retention Window

当前文档约定的 trace retention window 是 30 天，由 `SOCIALEASE_TRACE_RETENTION_DAYS` 配置。Terminal protocol 和 intervention-plan retention 由 `SOCIALEASE_PROTOCOL_RETENTION_DAYS` 配置。

当前 cleanup 会删除：

- 超过 trace retention window 的 trace rows；
- 状态为 `expired`、`rejected` 或 `consumed` 且超过 retention window 的 protocol rows；
- 状态为 `completed`、`cancelled` 或 `blocked` 且超过 retention window 的 intervention-plan rows。

真实试点前，需要确认备份/恢复预期，并确保隐私说明使用相同 retention window。
