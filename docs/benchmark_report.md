# SocialEase Agent Benchmark Report

> 2026-07-27 统一上下文完成基线：backend pytest `507 passed, 45 skipped`；311 条确定性
> Eval 全部通过；eval gate `passed`；PostgreSQL migration/runtime 与 Redis-backed
> task state 由 CI 服务容器验证。

本报告记录 SocialEase Agent 的确定性评测集，用于防止安全边界、产品边界和 agent workflow 在迭代中回退。它不是临床效果评估，不证明系统可以诊断、治疗或改善心理健康问题。

## 1. 评测目标

SocialEase 是面向大学生社交压力场景的安全可控 agent harness。Benchmark 回答的是工程问题：

- Safety floor 是否能拦截 crisis 和高风险表达；
- Intent router 是否能把请求路由到正确的有界 skill；
- RAG 是否给出 citation，并在不知道时避免编造资源；
- Role-play、worksheet、exposure 等 agent 是否保持结构化输出契约；
- 统一会话是否保持事件顺序、模块确认/嵌套、Crisis 抢占、owner scope、加密与删除级联；
- 隐私、consent replay、多用户访问和 unsafe progression 是否被拦截；
- 代码修改后完整工作流是否仍能跑通。

它的定位是 regression gate，不是临床有效性指标。

## 2. 运行方式

运行确定性 eval：

```bash
cd backend
python -m app.evals.run
```

该命令会同时写出 per-case eval trace artifact：

```text
backend/app/evals/reports/latest.json
backend/app/evals/reports/latest_failures.json
```

`latest.json` 包含本次运行的非敏感执行版本、确定性 JSONL 数据集内容 Hash，以及每条
eval case 的 suite、case_id、expected、actual、step 级结果和 failure reason；
`latest_failures.json` 只保留失败样例，便于 CI 或本地回归失败时快速定位。它和产品
`/api/runs/{run_id}` trace 不同：eval trace 是 synthetic benchmark 的调试产物，不记录
真实用户运行。

运行产品边界 gate：

```bash
cd backend
python -m app.evals.gate
```

检查 Prompt 版本治理：

```bash
make prompt-version-check
```

生产 Prompt 以 AST 指纹登记在 `backend/app/llm/prompt_versions.json`。修改 Prompt 后，先在
`backend/app/tracing/versions.py` 提升对应版本号，再运行：

```bash
make update-prompt-versions
```

更新命令会拒绝“Prompt 已变化但版本号未变化”的情况；GitHub Actions 还会与 PR 或推送前的
Manifest 比较，防止通过只手工刷新指纹绕过版本提升。

运行后端测试：

```bash
cd backend
pytest
```

仓库根目录：

```bash
make check
```

## 3. 当前结果

2026-07-27 本地与 CI 基线：

```text
backend pytest: 531 passed, 6 skipped
eval suite: all metrics passed
eval gate: passed
deterministic eval trace cases: 311 / 311 passed
PostgreSQL migration/runtime CI: passed
Redis-backed task state CI: passed
```

精确数字是该提交附近的快照；最新结果以当前 CI 和生成的 `latest.json` 为准。

核心 Eval 指标：

| Metric | Passed / Total | Score |
|---|---:|---:|
| safety accuracy | 5 / 5 | 1.000 |
| safety red-team pass rate | 9 / 9 | 1.000 |
| blocked crisis rate | 2 / 2 | 1.000 |
| intent accuracy | 7 / 7 | 1.000 |
| citation hit rate | 6 / 6 | 1.000 |
| retrieval recall@3 | 6 / 6 | 1.000 |
| retrieval MRR | 6 / 6 | 1.000 |
| unknown precision | 1 / 1 | 1.000 |
| memory retrieval recall@3 | 10 / 10 | 1.000 |
| memory false-recall avoidance | 14 / 14 | 1.000 |
| memory context token budget | 17 / 17 | 1.000 |
| roleplay feedback pass rate | 2 / 2 | 1.000 |
| worksheet extraction pass rate | 2 / 2 | 1.000 |
| E2E workflow pass rate | 5 / 5 | 1.000 |
| product-boundary pass rate | 210 / 210 | 1.000 |
| privacy redaction pass rate | 37 / 37 | 1.000 |
| consent replay resistance | 16 / 16 | 1.000 |
| cross-user access denial | 5 / 5 | 1.000 |
| continuation crisis detection | 10 / 10 | 1.000 |
| unsafe exposure progression block rate | 6 / 6 | 1.000 |
| stale plan cancellation rate | 4 / 4 | 1.000 |
| output guardrail hard-safety detection recall | 25 / 25 | 1.000 |
| output guardrail safe-allow precision | 15 / 15 | 1.000 |
| output guardrail repair recheck block rate | 2 / 2 | 1.000 |

`latest.json` 是完整指标和逐 case 结果的事实来源；上表只列核心发布边界，避免在文档中
复制全部派生指标。

## 4. 评测类别

### Safety

覆盖显式 crisis、红队变体、blocked crisis workflow。规则 safety floor 是确定性的，LLM classifier 只能提高风险等级，不能降低规则判断。

### Intent Routing

检查常见用户请求是否进入 support、role-play、worksheet、exposure planning、support-resource query、calendar planning 或 crisis escalation 等有界 action。

### RAG Grounding

检查相关 markdown 知识库是否被检索并返回 citation；unknown case 要明确不知道，不能编造学校、电话、热线或服务。

### 结构化 Agent 输出

Role-play feedback 和 worksheet extraction 测试保证输出保持 schema 和字段契约，让系统更接近结构化 agent workflow，而不是自由聊天回复。

### E2E Workflow

验证 Safety → Router → Permission → Skill → Trace 链路，确保 harness 选择正确 action，并记录可观察 trace。

### Product Boundary

当前 product-boundary 数据集有 210 条中文边界用例，覆盖：

- privacy redaction and minimization;
- implicit crisis expressions;
- colloquial self-harm expressions;
- bullying, stalking, and threat expressions;
- prompt injection resistance;
- confidential crisis requests;
- minor-safety boundaries;
- over-dependence on the agent;
- diagnosis, medication, and treatment-promise requests;
- consent replay resistance;
- cross-user access denial;
- continuation-turn crisis detection;
- unsafe exposure progression blocking;
- pause/stop-practice routing;
- stale intervention-plan cancellation;
- non-medical wording boundaries;
- trace output summary/minimization when assistant text may echo sensitive details;
- English/Chinese mixed crisis expressions;
- invite/consent and owner-scoped protocol regression cases;
- trace/privacy retention-relevant product boundaries.

## 5. Gate 策略

`python -m app.evals.gate` 是硬性回归 gate。当前要求关键产品边界指标保持 `1.000`。

如果 gate 失败：

- 不要为了展示或 CI 直接忽略；
- 先查看 `backend/app/evals/reports/latest_failures.json` 定位失败类别、expected/actual 和失败步骤；
- 修复 classifier、permission、privacy、RAG 或 workflow 行为；
- 只有产品边界预期确实改变时，才更新测试用例。

## 6. 不能证明什么

该 benchmark 不证明：

- 临床有效性；
- 诊断正确性；
- 治疗结果；
- 已经可以作为真实危机服务部署；
- 已满足法律、伦理或学校合规；
- 能抵御所有 prompt injection 或隐私攻击。

当前限制：

- 数据集仍偏小且确定性强；
- 多数 case 是手工构造；
- 还没有大规模人工标注红队集；
- 多语言、多轮覆盖仍有限；
- LLM 质量主要通过有界契约和 fallback 行为检查，而不是偏好评分。

## 7. 后续评测方向

- 将 210 条 product-boundary case 扩展成人工复核中文红队集；
- 增加 role-play、worksheet、exposure 的多轮对抗 case；
- 将 Support 输出中“LLM 提名候选片段、后端验证精确证据”的语义隐私检查扩展到其他生成型 Skill；
- 将 Prompt 版本提升从人工命名进一步演进为自动发布版本；
- 对比 deterministic fallback 与 LLM-enabled 运行；
- 为 role-play feedback 加入小规模人工质量 rubric。

## 8. Memory Vector/Hybrid 阶段四实验

该实验是引入 pgvector 前的离线门槛，不代表生产链路已经使用向量数据库。运行：

```bash
pip install -r backend/requirements-vector-eval.txt
make eval-memory-vector
```

固定配置：

- 数据：59 条中文 synthetic demo query。新增部分来自 12 组人工编写语义种子，
  每组扩展 3 个查询改写，共 36 条规模化查询；
- 规模：每个查询加入 2,048 条确定性安全 demo 干扰记忆，单查询最多 2,053 个
  候选；共 2,135 条去重索引文本；
- Classical 策略按生产 Repository 行为使用 owner/status/type/expiry 硬过滤后的
  100 条 recent candidate window；Vector/Hybrid 在相同硬过滤后评估完整候选集；
- Embedder：FastEmbed `0.8.0`；
- Model：`BAAI/bge-small-zh-v1.5`，512 维，revision
  `46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59`；
- 模型体积：约 90MB；
- Vector threshold：`0.50`，高于固定 no-memory hard-negative 的最高分
  `0.4791`；
- Hybrid：先通过 semantic threshold，再按 `0.75 semantic + 0.25 lexical`
  融合，遵循 Mem0“先语义门槛、再融合”的方向；
- 所有策略先执行用户、Consent、状态、类型、过期、安全内容和当前冲突过滤；
  场景是排序信号而不是硬过滤，Vector 不能先跨用户搜索再在应用层过滤。

2026-07-26 的 15 条小样本结果保留为历史基线：

| Strategy | Recall@3 | False Recall Avoidance | No-memory Abstention | Case Pass | Query P95 |
|---|---:|---:|---:|---:|---:|
| Recent | 0.5556 | 0.6000 | 0.0000 | 0.5333 | ~0.6ms |
| Metadata | 0.5556 | 0.8000 | 0.5000 | 0.6667 | ~0.2ms |
| SQL Text | 0.4444 | 0.9000 | 0.5000 | 0.6000 | ~0.3ms |
| Vector | 0.6667 | 0.8000 | 1.0000 | 0.8000 | ~4ms |
| Hybrid | 0.5556 | 0.8000 | 1.0000 | 0.7333 | ~6ms |

2026-07-29 规模扩展后的本地 CPU 结果：

| Strategy | Recall@3 | False Recall Avoidance | No-memory Abstention | Case Pass | Query P95 |
|---|---:|---:|---:|---:|---:|
| Recent | 0.0392 | 0.2778 | 0.0000 | 0.0169 | ~4ms |
| Metadata | 0.0392 | 0.2778 | 0.0000 | 0.0169 | ~3ms |
| SQL Text | 0.0392 | 0.6296 | 0.6667 | 0.1017 | ~2ms |
| Vector | 0.2745 | 0.8519 | 0.3333 | 0.2203 | ~85ms |
| Hybrid | 0.3529 | 0.7778 | 0.3333 | 0.2373 | ~77ms |

2,135 条 512 维 float32 向量的原始数据约 4.17 MiB；文档批量编码约 3.8 秒。
查询延迟包含本地 query embedding 和 Python 精确向量扫描，只用于方案间和数量级
比较，不等同于 pgvector ANN 的生产延迟。

结论：

- 规模扩大后，Vector/Hybrid 相比固定 100 条 SQL candidate window 的召回优势更加
  明显，说明语义候选生成在长历史中有工程价值。
- 但 Vector/Hybrid 的绝对 Recall、False Recall、No-memory Abstention 和 Case Pass
  均未达到生产安全门槛；Hybrid 提高召回的同时进一步降低 False Recall Avoidance。
- 当前 SQL Text 在超过 candidate window 的长历史中同样明显退化。继续使用它表示
  保持现有低复杂度、安全优先的生产基线，不表示其规模化质量已经达标。
- `vector_gate_met=false`，生产继续使用 SQL Text；阶段五 pgvector 暂缓。
- 后续若重开阶段五，应先实现安全的 lexical/scenario candidate union、
  polarity/conflict reranker，并补充独立人工标注集；不能通过降低语义阈值或只更换
  更大 embedding 模型绕过门槛。
