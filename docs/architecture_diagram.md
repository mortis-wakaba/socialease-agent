# SocialEase 架构图

这份图用于说明 SocialEase Agent 的系统边界和运行链路。项目定位是安全可控的 agent harness 产品化原型，而不是单轮聊天 prompt。

```mermaid
flowchart TD
    U[大学生用户] --> FE[Next.js 产品界面]
    FE --> API[FastAPI routes]

    API --> H[AgentHarness]
    API --> Direct[直接练习 API]

    H --> Ctx[RunContext<br/>认证 + 记忆上下文 + 请求上下文]
    H --> Safety[Safety Classifier<br/>规则底线 + 可选 LLM 只升不降]
    Safety --> Perm[Safety Permission Gate]
    Perm -->|crisis| Crisis[Crisis Escalation Flow]
    Perm -->|需要同意| Protocol[Consent Protocol<br/>请求哈希 + 过期 + 一次性消费]
    Protocol --> H
    Perm -->|允许/降级| Router[Intent Router]
    Router --> Registry[Skill Registry]

    Registry --> Support[Support Skill]
    Registry --> Roleplay[Role-play Skill]
    Registry --> Worksheet[CBT 风格自助练习 Skill]
    Registry --> Exposure[社交练习阶梯 Skill]
    Registry --> Resources[支持资源 Agent Loop Skill]
    Registry --> Clarify[Clarification Skill<br/>不执行状态变更]
    Registry --> Boundary[Out-of-scope Skill<br/>确定性产品边界]
    Registry --> Crisis

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

    H --> Privacy[Privacy Persistence Gate]
    RoleplayService --> Privacy
    WorksheetService --> Privacy
    ExposureService --> Privacy

    Privacy --> Memory[User Memory<br/>摘要 + 偏好 + 导出/删除]
    Privacy --> DB[(SQLite 本地开发库)]
    Protocol --> DB
    Memory --> DB

    DB -. 生产化目标 .-> PG[(PostgreSQL runtime adapters<br/>+ Alembic migrations)]

    H --> Trace[Trace Logger]
    Trace --> Metrics[Metrics Backend]
    Trace --> Eval[Eval Gate<br/>安全 + 隐私 + 同意 + 归属边界]

    FE --> TraceUI["/trace 开发诊断视图"]
    FE --> PracticeUI["/practice rubric feedback"]
    TraceUI --> Trace
    PracticeUI --> RoleplayService
```

## 讲解口径

- 前端是产品体验入口；真正的安全边界放在后端 harness、permission gate 和服务层 safety floor。
- LLM 是可选增强，不是系统唯一依赖；关闭 API key 时仍可用 deterministic fallback 跑完整流程。
- Crisis 输入绕过普通 agent，进入危机转介流程。
- 信息不足时先澄清，明确领域外请求返回产品边界；这两个分支不创建练习计划。
- 会改变用户状态的主动练习必须经过权限判断和 consent protocol。
- Role-play feedback 使用隐私安全的派生特征和 rubric，不把长期原始对话作为记忆基础。
- Worksheet 使用结构化草稿和少量补充回答，Support 使用查询与 Citation 指代状态；Progress 继续使用 Plan/Task/Attempt，不复制聊天窗口。
- SQLite 用于本地开发和展示；PostgreSQL adapter、Alembic、迁移检查和 repository factory 是生产化路径。
- Trace、metrics 和 eval gate 让安全、隐私、同意和多用户边界可观察、可回归。
