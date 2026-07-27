# ADR 0009：旧角色扮演记录只读导入统一时间线

- 状态：Accepted
- 日期：2026-07-27

## 决策

旧 `roleplay_sessions` 不参与新旧接口双写。统一对话页首次载入时调用幂等导入接口，
服务端把当前用户已有的角色扮演 Session 投影为一条已归档 Conversation：

- Conversation、ModuleRun 和 Event ID 由用户 ID 与旧 Session ID 确定性生成；
- Conversation 为 `archived`，ModuleRun 为终态，不能继续发送消息；
- 原消息顺序、角色和时间戳保留；
- 整个 Snapshot 在单个数据库事务内写入；
- 重复调用返回同一 Conversation，不产生重复时间线；
- 旧领域表仍是导入来源，不接收来自 Conversation 的反向双写。

旧 `/api/chat` 与 `/api/chat/stream` 在 OpenAPI 和响应头中标记 deprecated，
并指向 `/api/conversations`。旧领域读取接口继续用于详情和迁移期兼容；新产品入口只使用
Conversation API。

## 原因

运行时双写会制造顺序、删除和失败恢复上的漂移。确定性、事务化、只读投影能让已有记录
继续可见，同时保持新会话架构只有一个写入事实来源。

## 后果

- 导入失败不会留下半条时间线；
- 已导入旧记录只能查看、导出或删除；
- 迁移稳定后可以独立评估旧领域写接口的外部消费者，再决定移除日期。
