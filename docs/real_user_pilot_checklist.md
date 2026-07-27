# SocialEase 真实用户试点 Checklist

> 目标：小范围、可回滚、有人负责地验证 SocialEase 作为非医疗社交练习工具的真实可用性。  
> 禁止表述：不要称为医疗产品、诊断工具、治疗工具或危机服务。

## 试点前

### 产品边界

- [ ] 页面和 README 明确：不诊断、不治疗、不替代心理咨询。
- [ ] crisis flow 已测试：自伤、自杀、伤害他人、严重危机表达会暂停普通练习。
- [ ] 校园资源回答来自知识库引用，不编造电话、热线或机构。
- [ ] `/privacy` 和 `/terms` 页面已与当前后端行为一致。

### 账号和访问

- [ ] `SOCIALEASE_AUTH_MODE=production`
- [ ] `SOCIALEASE_ENABLE_SIGNUP=false`
- [ ] 配置 `SOCIALEASE_SIGNUP_ALLOWED_EMAILS` 或 `SOCIALEASE_SIGNUP_INVITE_CODES`
- [ ] 邀请码或 allowlist 只发给试点用户。
- [ ] 普通用户无法访问其它用户的 profile、history、memory export/delete。

### 数据和隐私

- [ ] 阅读并确认 `docs/data_retention_and_privacy.md`
- [ ] 配置 secret manager 中的 `SOCIALEASE_CONVERSATION_CONTENT_KEY` 和非秘密版本标签。
- [ ] 已验证缺少或错误的会话内容密钥会使 production 的 Conversation 持久化初始化失败。
- [ ] 设置 `SOCIALEASE_TRACE_RETENTION_DAYS`
- [ ] 设置 `SOCIALEASE_PROTOCOL_RETENTION_DAYS`
- [ ] 跑过一次 cleanup dry-run。
- [ ] 跑过 memory export/delete 测试。
- [ ] 跑过单个/全部 Conversation 的查看、导出和删除测试，并确认删除级联范围。
- [ ] 确认备份策略不会违反删除承诺。

### 数据库和部署

- [ ] PostgreSQL 已执行 `alembic upgrade head`
- [ ] `SOCIALEASE_REQUIRE_REDIS=true`，三类 Task State readiness probe 均通过。
- [ ] `/ready` 返回 ready。
- [ ] `docker compose -f docker-compose.prod.yml --env-file .env.production config` 通过。
- [ ] 备份脚本和恢复演练至少跑通一次。
- [ ] 阅读并确认 `docs/monitoring_backup_and_alerting_checklist.md`
- [ ] alert webhook 或人工值班方式已确定。

### 评测

- [ ] `pytest` 通过。
- [ ] `python -m app.evals.gate` 通过。
- [ ] `docs/benchmark_report.md` 已更新到当前 eval gate 输出。
- [ ] Postgres integration tests 通过。
- [ ] crisis、privacy、consent replay、cross-user boundary 样例通过。
- [ ] Proposal 接受/拒绝、模块嵌套/结束、Crisis 抢占和完整历史恢复样例通过。

## 试点中

- [ ] 记录参与人数、开放时间、负责人。
- [ ] 监控 crisis runs、fallback runs、rate-limit hits、slow requests。
- [ ] 不收集不必要的敏感心理细节。
- [ ] 用户反馈说明用途和保留期限。
- [ ] 出现异常时能关闭注册、关闭 LLM 或切维护提示。

## 试点后

- [ ] 导出 aggregate metrics，不导出用户原文。
- [ ] 复盘 safety / privacy / usability 问题。
- [ ] 删除或匿名化不再需要的试点数据。
- [ ] 更新 README、demo walkthrough 和产品边界说明。
- [ ] 不夸大效果，不写“治疗”“诊断”“治愈”等表述。

## 推荐命令

```bash
cd backend
pytest
python -m app.evals.gate
python -m app.jobs.cleanup_scheduler --run-once --dry-run
```

```bash
SOCIALEASE_DATABASE_URL=postgresql+psycopg://... alembic upgrade head
SOCIALEASE_DATABASE_URL=postgresql+psycopg://... python -c "import app.main; print('import ok')"
SOCIALEASE_TEST_DATABASE_URL=postgresql+psycopg://... pytest tests/test_postgres_*.py
```
