# SocialEase Agent Benchmark Report

> 当前基线：backend pytest `307 passed, 26 skipped`；eval suite `all metrics passed`；eval gate `passed`。

本报告记录 SocialEase Agent 的确定性评测集，用于防止安全边界、产品边界和 agent workflow 在迭代中回退。它不是临床效果评估，不证明系统可以诊断、治疗或改善心理健康问题。

## 1. 评测目标

SocialEase 是面向大学生社交压力场景的安全可控 agent harness。Benchmark 回答的是工程问题：

- Safety floor 是否能拦截 crisis 和高风险表达；
- Intent router 是否能把请求路由到正确的有界 skill；
- RAG 是否给出 citation，并在不知道时避免编造资源；
- Role-play、worksheet、exposure 等 agent 是否保持结构化输出契约；
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

`latest.json` 包含每条 eval case 的 suite、case_id、expected、actual、step 级结果和 failure reason；`latest_failures.json` 只保留失败样例，便于 CI 或本地回归失败时快速定位。它和产品 `/api/runs/{run_id}` trace 不同：eval trace 是 synthetic benchmark 的调试产物，不记录真实用户运行。

运行产品边界 gate：

```bash
cd backend
python -m app.evals.gate
```

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

最近本地基线：

```text
backend pytest: 307 passed, 26 skipped
eval suite: all metrics passed
eval gate: passed
```

Eval 指标：

| Metric | Passed / Total | Score |
|---|---:|---:|
| safety accuracy | 5 / 5 | 1.000 |
| safety red-team pass rate | 9 / 9 | 1.000 |
| blocked crisis rate | 2 / 2 | 1.000 |
| intent accuracy | 6 / 6 | 1.000 |
| citation hit rate | 6 / 6 | 1.000 |
| retrieval recall@3 | 6 / 6 | 1.000 |
| retrieval MRR | 6 / 6 | 1.000 |
| unknown precision | 1 / 1 | 1.000 |
| roleplay feedback pass rate | 2 / 2 | 1.000 |
| worksheet extraction pass rate | 2 / 2 | 1.000 |
| E2E workflow pass rate | 4 / 4 | 1.000 |
| product-boundary pass rate | 210 / 210 | 1.000 |
| privacy redaction pass rate | 21 / 21 | 1.000 |
| consent replay resistance | 5 / 5 | 1.000 |
| cross-user access denial | 5 / 5 | 1.000 |
| continuation crisis detection | 10 / 10 | 1.000 |
| unsafe exposure progression block rate | 6 / 6 | 1.000 |
| stale plan cancellation rate | 4 / 4 | 1.000 |

## 4. 评测类别

### Safety

覆盖显式 crisis、红队变体、blocked crisis workflow。规则 safety floor 是确定性的，LLM classifier 只能提高风险等级，不能降低规则判断。

### Intent Routing

检查常见用户请求是否进入 support、role-play、worksheet、exposure planning、support-resource query 或 crisis escalation 等有界 action。

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
- 增加语义 PII 检测，不只看“电话”“地址”等显式标签；
- 在 eval report 中记录 prompt/model 版本；
- 对比 deterministic fallback 与 LLM-enabled 运行；
- 为 role-play feedback 加入小规模人工质量 rubric。
