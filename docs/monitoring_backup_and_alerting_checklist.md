# SocialEase 监控、告警与备份恢复 Checklist

> 目标：真实试点前，确认系统可以被观察、备份、恢复和回滚。  
> 边界：监控只使用 aggregate metrics，不导出用户原文、run id、session id 或账号标识。

## 监控项

| 信号 | 来源 | 默认阈值环境变量 | 处理方式 |
|---|---|---|---|
| readiness failed | `/ready` | 不适用 | 暂停发布，检查数据库、migration graph 和三类 Redis Task State probe |
| crisis runs spike | `/api/harness/metrics` | `SOCIALEASE_ALERT_CRISIS_RUNS` | 人工复核 crisis flow 和试点支持流程 |
| fallback runs spike | metrics | `SOCIALEASE_ALERT_FALLBACK_RUNS` | 检查 LLM provider、网络和 fallback 是否安全 |
| rate limit hits | metrics | `SOCIALEASE_ALERT_RATE_LIMIT_HITS` | 检查异常流量或试点用户操作模式 |
| LLM saturation | metrics | `SOCIALEASE_ALERT_LLM_CONCURRENCY_SATURATION` | 降级到 deterministic fallback 或提高 provider 容量 |
| slow requests | metrics | `SOCIALEASE_ALERT_SLOW_REQUESTS` | 检查数据库、provider timeout、部署资源 |
| p95 latency | metrics | `SOCIALEASE_ALERT_LATENCY_P95_MS` | 检查瓶颈并决定是否限流或降级 |

运行 dry-run：

```bash
SOCIALEASE_MONITOR_BASE_URL=http://127.0.0.1:8000 \
python scripts/monitor_alerts.py --dry-run
```

发送 webhook：

```bash
SOCIALEASE_MONITOR_BASE_URL=https://api.example.edu \
SOCIALEASE_ALERT_WEBHOOK_URL=https://alerts.example.edu/socialease \
python scripts/monitor_alerts.py
```

## 备份

PostgreSQL：

```bash
SOCIALEASE_DATABASE_URL=postgresql+psycopg://... \
SOCIALEASE_BACKUP_DIR=/secure/socialease-backups \
bash scripts/backup_database.sh
```

SQLite 本地开发库：

```bash
SOCIALEASE_DB_PATH=backend/socialease.db \
bash scripts/backup_database.sh
```

要求：

- 备份目录不提交 Git；
- 备份文件应放在加密磁盘或私有对象存储；
- 备份访问要有最小权限；
- 试点删除承诺要和备份保留周期一致。

## 恢复演练

SQLite：

```bash
bash scripts/restore_drill.sh backups/socialease-sqlite-YYYYMMDDTHHMMSSZ.db
```

PostgreSQL：

```bash
SOCIALEASE_RESTORE_TEST_DATABASE_URL=postgresql+psycopg://... \
bash scripts/restore_drill.sh backups/socialease-postgres-YYYYMMDDTHHMMSSZ.dump
```

要求：

- 不要把 restore drill 跑到生产库；
- 恢复演练后检查 `/ready`；
- 恢复演练结果记录在受控的内部运维环境中，不写入公开仓库。

## 试点前通过标准

- [ ] `/ready` 通过；
- [ ] `python scripts/monitor_alerts.py --dry-run` 可运行；
- [ ] 备份脚本成功生成文件；
- [ ] restore drill 在测试库或临时 SQLite 文件通过；
- [ ] cleanup scheduler dry-run 可运行；
- [ ] 告警接收人和值班流程已确认；
- [ ] 所有监控和告警不包含用户原文。
