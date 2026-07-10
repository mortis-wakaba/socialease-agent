# SocialEase 数据保留与隐私说明

> 适用范围：SocialEase Agent 真实用户试点准备。  
> 产品边界：SocialEase 不是医疗产品，不做诊断，不替代心理咨询或紧急服务。

## 数据分类

| 数据类别 | 示例 | 是否可能包含用户原文 | 当前保护方式 | 用户控制 |
|---|---|---:|---|---|
| 账号数据 | email、密码哈希、session/token id | 否 | 密码只保存哈希；refresh token 保存 hash | 可退出试点；后续可扩展账号删除 |
| 练习记录 | roleplay session、worksheet、exposure plan | 部分字段可能来自用户输入 | privacy persistence gate、敏感信息脱敏、raw text 最小化 | `/settings` 导出/删除 |
| Trace | safety、intent、permission、selected agent、输出摘要 | 默认不应保存完整原始心理文本 | trace field policy、最小化 input/output | retention cleanup |
| Protocol | consent request、approval/rejection/consumed 状态 | 否，主要是动作和请求绑定 | protocol id、request hash、过期时间 | 过期/终态后 cleanup |
| Intervention Plan | 练习动作步骤、状态、结果摘要 | 少量派生信息 | session-level plan，不作为治疗方案 | 终态后 cleanup |
| Metrics | 聚合计数、latency、fallback、rate limit hit | 否 | 不含用户原文和账号标识 | 仅 aggregate |
| 长期偏好 | 反馈风格、偏好场景、难度 | 低敏感 | 明确同意后保存 | `/settings` 关闭 |

## 保留策略

默认试点配置：

- `SOCIALEASE_TRACE_RETENTION_DAYS=30`
- `SOCIALEASE_PROTOCOL_RETENTION_DAYS=30`
- `SOCIALEASE_ABANDONED_PLAN_MINUTES=60`

cleanup job 会删除：

- 超过 trace retention window 的 `runs` rows；
- 终态 protocol rows：`expired`、`rejected`、`consumed`；
- 终态 intervention plan rows：`completed`、`cancelled`、`blocked`。

用户主动删除记忆时，会删除 user-owned records：

- `runs`
- `roleplay_sessions`
- `worksheets`
- `exposure_plans`
- `exposure_attempts`
- `protocols`
- `intervention_plans`
- `user_memory_settings`

## 用户控制

用户应能在产品中完成：

- 查看轻量 profile summary；
- 导出本人练习记录；
- 删除本人练习记录；
- 关闭长期练习偏好；
- 了解系统不是医疗产品、不做诊断、不承诺效果。

## 试点前必须确认

- 隐私说明页面与后端真实行为一致；
- retention window 已由试点负责人确认；
- backup/restore 策略不会和删除承诺冲突；
- 不在演示或产品流程中编造学校电话、热线或联系人；
- crisis flow 建议现实支持，但不承诺替代紧急服务；
- 如进入正式校园试点，应完成机构、法律、隐私和安全审核。
