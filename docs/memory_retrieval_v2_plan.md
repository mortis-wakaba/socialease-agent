# 长期记忆检索 v2：分阶段实施与验证计划

## 目标与上线边界

本计划只处理已经通过写入策略、保存在 `episodic_memories` 中的短摘要。
长期记忆不是知识库文档，不做文档加载或分块。当前 SQL Text 继续作为生产默认；
v2 只有在完整消融实验和隐私 Gate 通过后才允许接管生产流量。

所有检索通道必须先应用同一套应用层硬过滤。数据库查询条件是第一道边界，
应用层策略是防止 adapter、索引或未来实现错误的第二道边界。模型不得决定
`user_id`、生命周期、允许的记忆类型或 consent。

## 阶段 1：硬过滤

建立独立、数据库无关的 `MemoryHardFilter`：

- 强制匹配用户、允许的状态和记忆类型；
- 排除过期、敏感、危机、诊断、提示注入内容；
- 当前请求与历史记忆冲突时，以当前请求为准；
- 过滤在所有召回通道之前执行，并在 rerank 后再次校验；
- 只输出枚举化拒绝原因和计数，不输出 query 或 summary。

验证：跨用户、过期、归档、类型、PII、危机、注入和否定冲突的参数化测试。

## 阶段 2：四路召回

召回只处理过滤后的 `EpisodicMemoryRecord`：

1. Dense Vector：通过 `DenseEmbeddingProvider` Protocol 注入；评测 adapter 使用
   固定版本的中文 BGE。核心包不依赖具体向量数据库。
2. BM25：对用户级过滤后语料计算词法相关性。生产规模增长后可以由 PostgreSQL
   FTS adapter 实现相同的词法召回 Protocol；Service 不拼 SQL。
3. Metadata：使用 thread、scenario、skill、recency 和 confidence，不能单独越过
   最低相关性约束。
4. Multi-Query Expansion：确定性、有上限地生成原查询、连续性查询和技能/场景查询。
   扩展查询继承原查询中的否定语义；扩展结果仍需经过相同硬过滤。

每一路最多返回固定数量候选。候选按 `memory_id` 去重，用 Reciprocal Rank Fusion
合并，避免把余弦、BM25 和元数据的原始分数当成同一量纲直接相加。

验证：每一路独立契约测试、去重测试、扩展上限测试，以及 hard negative 测试。

## 阶段 3：Cross-Encoder rerank 与隐私

定义数据库和模型无关的 `MemoryReranker` Protocol。真实评测 adapter 使用
FastEmbed Cross-Encoder；单元测试使用确定性替身。只 rerank 合并后的前 20 条候选。

最终分数使用校准到 `[0, 1]` 的分量：

- Cross-Encoder：0.60；
- 归一化 RRF：0.20；
- Dense：0.08；
- BM25：0.06；
- Metadata：0.06。

权重由校验模型管理且总和必须为 1。模型输入只包含当前 query 和已经最小化的
memory summary，不包含 user id、联系方式、原始聊天或其他租户内容。模型输出不能
修改过滤作用域。Trace 只保存内容无关的分数、rank、provider 和模型版本。

验证：候选上限、权重和分数范围、rerank 后二次过滤、敏感内容不进入 adapter、
诊断信息不含正文。

## 阶段 4：Abstention

在最终返回前建立显式拒答策略：

- 没有合格候选；
- top-1 低于绝对阈值；
- top-1 与 top-2 间隔过小且二者语义冲突；
- query 的关键约束没有被候选覆盖；
- 当前请求与候选冲突；
- reranker 不可用或返回无效分数时，不静默降级为“已通过 v2”。

返回枚举化 `AbstentionReason`。拒答是正常结果，不是异常。

## 阶段 5：消融实验

在同一固定数据集、同一过滤策略和同一 token budget 上比较：

1. SQL Text baseline；
2. Dense only；
3. BM25 only；
4. Dense + BM25 + Metadata；
5. 上述方案 + Multi-Query；
6. 上述方案 + Cross-Encoder；
7. 完整方案 + Abstention。

核心指标：

- relevant recall@3、MRR、case pass rate；
- false/stale/conflict/cross-user recall avoidance；
- no-memory abstention precision/recall；
- p50/p95 延迟、候选数和模型调用数；
- reranker 输入条数上限与内容泄漏检查。

采用条件：安全指标必须为 1.0；拒答指标不得低于 SQL baseline；recall@3 至少提高
0.10；case pass rate 不下降；预热 p95 不超过 250 ms。未通过时继续保留 SQL Text。

## 阶段 6：扩展数据集

数据仍使用标记为 demo 的 JSONL，并扩展以下类型：

- 同义改写、口语和隐式意图；
- 高词面重叠但含义错误的 hard negative；
- 否定、偏好变化、旧记忆被当前请求覆盖；
- 同场景不同技能、跨场景可迁移技能和线程连续性；
- 跨用户、过期、归档、PII、危机和提示注入；
- 无相关记忆和多个近似候选；
- 超过 2,000 条用户级候选的规模测试。

开发集用于阈值校准，held-out 集只用于最终 Gate，避免用同一批案例同时调参和验收。

## 分批提交与验证

每个阶段独立提交：

1. `docs(memory): define retrieval v2 rollout plan`
2. `feat(memory): centralize retrieval hard filters`
3. `feat(memory): add multi-route recall and query expansion`
4. `feat(memory): add private cross-encoder reranking`
5. `feat(memory): add explicit retrieval abstention`
6. `eval(memory): add retrieval ablations`
7. `eval(memory): expand held-out retrieval dataset`

每阶段先运行对应单元测试；最后运行 backend 全量测试、隐私检查、既有 memory eval
和新 v2 消融。模型依赖不可用时，模型评测必须明确标记未执行，不能报告通过。

## 实施记录（2026-07-29）

阶段 1–6 已作为独立候选链路实现，生产 `EpisodicMemoryRetriever` 仍默认使用
SQL Text。当前没有引入 pgvector 或其他向量数据库。

- 新增统一 `MemoryHardFilter` 和无正文拒绝原因；
- 新增 Dense、BM25、Metadata、Multi-Query 召回及 RRF 融合；
- 新增本地 FastEmbed BGE Cross-Encoder adapter、校验权重和二次硬过滤；
- 新增显式 Abstention policy；
- 新增七组消融 runner 和 `make eval-memory-ablation`；
- 新增 16 条 held-out 人工案例。完整评测共 75 条 query、2,160 条去重摘要，
  每个规模案例包含 2,048 条背景记忆。

确定性 embedding/reranker 替身的全规模运行只验证了评测器的规模、指标和 Gate
契约，`adoption_gate_met=false`，不能代表真实模型效果。真实
`BAAI/bge-reranker-base` 首次下载因执行环境无法连接 Hugging Face 而未完成，
因此真实 Cross-Encoder Gate 状态是“未评测”，不是“通过”。

验证结果：

- PostgreSQL 全量后端：`554 passed, 6 skipped`；
- Repository privacy check：通过；
- deterministic eval gate：通过；
- Prompt version check：通过（本次未修改生产 Prompt）；
- migration discipline check：通过（本次未修改 schema）。

下一决策点：在可访问模型缓存的环境运行 `make eval-memory-ablation`。只有 held-out
安全指标全部为 1.0、相关性增益和延迟 Gate 同时通过，才进入 PostgreSQL + pgvector
adapter 的存储设计；否则继续使用 PostgreSQL SQL Text，并针对失败的消融层调优。
