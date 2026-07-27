# SocialEase 架构图

这份图用于说明 SocialEase Agent 的系统边界和运行链路。项目定位是安全可控的 agent harness 产品化原型，而不是单轮聊天 prompt。

```mermaid
flowchart TD
    U[大学生用户] --> FE[Next.js 产品界面]
    FE --> ConversationUI["/chat 统一对话与完整历史"]
    FE --> DetailUI[旧领域详情页<br/>迁移兼容 / 深链接]
    FE --> API[FastAPI routes]

    ConversationUI --> ConversationAPI[Conversation API]
    ConversationAPI --> ConversationService[Conversation Service<br/>Owner + 顺序 + 幂等]
    ConversationService --> ConversationSafety[每轮 Safety Classifier]
    ConversationSafety --> ConversationCrisis{Crisis Preemption}
    ConversationCrisis -->|crisis| ModulePreempt[终止模块栈 + Crisis Event]
    ConversationCrisis -->|ordinary| ConversationRouter[Intent Router]
    ConversationRouter -->|普通交流| H[AgentHarness]
    ConversationRouter -->|适合模块| Proposal[Module Proposal<br/>只提供选项]
    Proposal -->|用户接受| Stack[Module Stack Policy<br/>最大深度 3 + 组合白名单]
    Proposal -->|用户拒绝| ConversationService
    Stack --> Coordinator[Module Coordinator]
    Coordinator --> Adapters[Role-play / Worksheet / Exposure / Resource Adapters]
    Adapters --> ConversationService
    Adapters --> RoleplayService
    Adapters --> WorksheetService
    Adapters --> ExposureService
    Adapters --> SupportResourceService

    API --> H
    API --> Direct[旧领域 API<br/>详情与迁移兼容]
    API --> CalendarAPI[Calendar API]

    H --> Ctx[RunContext<br/>认证 + 记忆上下文 + 请求上下文]
    H --> Safety[Safety Classifier<br/>规则底线 + 可选 LLM 只升不降]
    Safety --> CrisisCheck{Crisis Preemption}
    CrisisCheck -->|crisis| Crisis[Crisis Escalation Flow]
    CrisisCheck -->|ordinary| Router[Intent Router]
    Router --> ActionMap[Intent → HarnessAction]
    ActionMap --> Perm[Safety Permission Gate]
    Perm -->|需要同意| Protocol[Consent Protocol<br/>请求哈希 + 过期 + 一次性消费]
    Protocol --> H
    Perm -->|允许/降级/阻断| Registry[Skill Registry]

    Registry --> Support[Support Skill]
    Registry --> Roleplay[Role-play Skill]
    Registry --> Worksheet[CBT 风格自助练习 Skill]
    Registry --> Exposure[社交练习阶梯 Skill]
    Registry --> Resources[支持资源 Agent Loop Skill]
    Registry --> Clarify[Clarification Skill<br/>不执行状态变更]
    Registry --> Boundary[Out-of-scope Skill<br/>确定性产品边界]
    Registry --> CalendarSkill[Calendar Planning Skill<br/>只生成提醒提案]
    Registry --> Crisis

    Support --> OutputGuardrail[全局 Output Guardrail<br/>Allow / One-shot Repair / Replace]
    Roleplay --> OutputGuardrail
    Worksheet --> OutputGuardrail
    Exposure --> OutputGuardrail
    Resources --> OutputGuardrail
    Clarify --> OutputGuardrail
    Boundary --> OutputGuardrail
    CalendarSkill --> OutputGuardrail
    Crisis --> OutputGuardrail
    OutputGuardrail -->|repair| Recheck[Repair 二次 Guardrail]
    OutputGuardrail -->|allow / replace| Privacy
    Recheck --> Privacy

    Direct --> DirectSafety[服务层 Safety Floor]
    DirectSafety --> RoleplayService[Roleplay Service]
    DirectSafety --> WorksheetService[Worksheet Service]
    DirectSafety --> ExposureService[Exposure Service]
    DirectSafety --> SupportResourceService[Support Resource Service]

    RoleplayService --> SocialRAG[Social Skills RAG]
    RoleplayService --> RedisContext[(Redis 短期会话 Context<br/>TTL + Dynamic Window + Compact)]
    WorksheetService --> RedisTask[(Redis Typed Task Sessions<br/>Worksheet Draft / Support Search)]
    SupportResourceService --> RedisTask
    WorksheetService --> SocialRAG
    ExposureService --> SocialRAG
    Resources --> ResourceLoop[最多 3 步只读 Loop<br/>资源检索 / 练习指导 / Finish]
    ResourceLoop --> ResourceRAG[已验证公开资源 RAG]
    ResourceLoop --> SocialRAG
    SupportResourceService --> ResourceRAG
    Support --> PolicyRAG[Safety Policy / 产品 Rubric]
    Roleplay --> PolicyRAG

    CalendarAPI --> CalendarConsent[Owner-bound Consent<br/>请求哈希 + 一次性消费]
    CalendarConsent --> CalendarService[Calendar Service<br/>幂等 + 创建后回读]
    CalendarService --> MCPClient[Calendar MCP Client]
    MCPClient --> MCPServer[Calendar MCP Server]
    MCPServer --> Provider[CalendarProvider Adapter]
    Provider --> DemoProvider[内存 Demo Provider]
    Provider -. 真实厂商扩展 .-> ExternalCalendar[Google / Microsoft 等]

    H --> Privacy[Privacy Persistence Gate]
    ConversationService --> ConversationContext[Working Context<br/>Token Budget + Compact Summary]
    ConversationService --> ConversationHistory[Append-only Timeline<br/>默认保留到用户删除]
    ConversationHistory --> ContentProtection[AES-256-GCM<br/>production fail closed]
    ConversationContext --> Factory
    ContentProtection --> Factory
    RoleplayService --> Privacy
    WorksheetService --> Privacy
    ExposureService --> Privacy

    Privacy --> Memory[User Memory<br/>摘要 + 偏好 + 导出/删除]
    Privacy --> Factory[RepositoryFactory]
    Protocol --> Factory
    Memory --> Factory
    Factory --> SQLite[(SQLite<br/>本地开发)]
    Factory --> PG[(PostgreSQL<br/>生产运行 + Alembic)]

    H --> Trace[Trace Logger]
    Trace --> Metrics[Metrics Backend]
    Trace --> Eval[Eval Gate<br/>安全 + 隐私 + 同意 + 归属边界]
    Cleanup[Cleanup Scheduler] --> CleanupLock[PostgreSQL Advisory Lock]
    CleanupLock --> PG

    FE --> TraceUI["/trace 开发诊断视图"]
    TraceUI --> Trace
    DetailUI --> Direct
```

## 讲解口径

- `/chat` 是唯一主对话入口，普通交流与模块共用一个 Conversation ID、时间线和输入框；
  `/practice`、`/worksheet` 等旧页面只保留为迁移期详情或深链接，不再承担主流程导航。
- LLM 只能生成严格校验的 Module Proposal；用户接受后才能进入模块。模块由用户结束，
  允许白名单内嵌套，结束子模块后恢复父模块。
- 完整历史、模型 Working Context 和长期 Agent Memory 分离：用户能查看完整时间线，
  模型只读取有界投影，历史不会自动成为长期记忆。
- 前端是产品体验入口；真正的安全边界放在 Conversation Service、harness、permission gate 和服务层 safety floor。
- LLM 是可选增强，不是系统唯一依赖；关闭 API key 时仍可用 deterministic fallback 跑完整流程。
- Crisis 输入绕过普通 agent，进入危机转介流程。
- Crisis 在任意模块深度抢占当前流程并终止栈；不能因 Adapter 清理失败继续练习。
- 信息不足时先澄清，明确领域外请求返回产品边界；这两个分支不创建练习计划。
- 会改变用户状态的主动练习必须经过权限判断和 consent protocol。
- Role-play feedback 使用隐私安全的派生特征和 rubric；Redis 只在 TTL 内保存受 Token Budget 约束的任务上下文，不把原始对话提升为长期记忆。
- Worksheet 使用结构化草稿和少量补充回答，Support 使用查询与 Citation 指代状态；Progress 继续使用 Plan/Task/Attempt，不复制聊天窗口。
- Calendar Skill 只生成提案；创建、修改和删除由独立 API 在 Consent 后通过 MCP 完成，当前 Provider 是 Demo，真实厂商需要单独实现 OAuth 和 Adapter。
- SQLite 用于本地开发和展示；RepositoryFactory 在生产运行时选择 PostgreSQL，并由 Alembic 和集成测试验证迁移与主要 Repository 路径。
- 全局 Output Guardrail 位于 Skill 之后、Memory/Trace/API 返回之前；Repair 最多一次，并对同一修复文本再次检查。
- PostgreSQL cleanup scheduler 使用 advisory lock，避免多个副本重复执行同一轮清理。
- Conversation History 不受 Trace/Protocol cleanup window 影响，默认保留到用户主动删除；
  production 会话正文使用 AES-256-GCM，缺少密钥时拒绝持久化启动。
- Trace、metrics 和 eval gate 让安全、隐私、同意和多用户边界可观察、可回归。
