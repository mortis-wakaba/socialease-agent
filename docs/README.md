# SocialEase 文档入口

本目录只保留长期有用的设计、运行、评测、部署和演示材料。已完成的阶段性计划文档不再保留，避免误导后续开发和维护。

## 推荐阅读顺序

1. [`architecture_diagram.md`](architecture_diagram.md)：系统架构图。
2. [`agent_harness_design.md`](agent_harness_design.md)：Agent Harness、skills、permission、memory 和 trace 设计。
3. [`benchmark_report.md`](benchmark_report.md)：评测目标和当前基线。
4. [`production_readiness.md`](production_readiness.md)：生产化能力和剩余差距。

## 运行和部署

- [`environment_config.md`](environment_config.md)：环境变量说明。
- [`deployment_runbook.md`](deployment_runbook.md)：部署、回滚、健康检查和运维流程。
- [`migrations.md`](migrations.md)：数据库迁移规范。
- [`cleanup_scheduler.md`](cleanup_scheduler.md)：清理任务和 retention 行为。
- [`load_tests.md`](load_tests.md)：负载测试说明。
- [`monitoring_backup_and_alerting_checklist.md`](monitoring_backup_and_alerting_checklist.md)：监控、备份和告警检查表。

## 安全、隐私和试点

- [`data_retention_and_privacy.md`](data_retention_and_privacy.md)：数据保留、导出和删除范围。
- [`real_user_pilot_checklist.md`](real_user_pilot_checklist.md)：真实用户试点前检查清单。
- [`human_review_rubric.md`](human_review_rubric.md)：人工抽样 review rubric。
- [`knowledge_base_design.md`](knowledge_base_design.md)：公开知识库、内部知识库和 citation 边界。

试点 review 记录可能包含内部运维信息或用户反馈，不保存在公开仓库中。

## 架构决策

ADR 保存在 [`adr/`](adr/)：

- hybrid safety / permission gate；
- skill registry；
- grounded support resources；
- OpenAI-compatible LLM fallback；
- executable skill dispatch；
- product-boundary eval；
- production database and boundary gates。
