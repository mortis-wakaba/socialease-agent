# SocialEase Agent Benchmark Report

> 当前可复现基线：312 条确定性 Eval 全部通过；eval gate `passed`；PostgreSQL
> migration/runtime 与 Redis-backed task state 由 CI 服务容器验证。pytest 精确数量随测试集
> 演进，不作为长期能力声明，以当前 CI 为准。

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

运行会话输入到输出的分阶段延迟评测（需要已迁移的 PostgreSQL；是否启用本地路由模型由
`LOCAL_LLM_*` 配置决定）：

```bash
make eval-conversation-latency
```

报告写入 `/tmp/socialease-conversation-latency-report.json`，包含总耗时与首个输出的
average、p50、p95、min、max，并按以下层级拆分：

- `total` → command claim、turn execution、command complete；
- `turn_execution` → turn preparation、turn dispatch；
- `turn_preparation` → state load、safety、input persistence；
- `turn_dispatch` → routing、context、workflow、response persistence/assembly；
- `workflow` → Harness safety、routing、skill、output guardrail、trace。

默认会分别输出四个场景，不能混合计算一个 p95：

- `first_process_request`：benchmark 进程的第一次请求；若评测程序没有控制模型进程启停，
  它不能冒充真正的模型冷启动，报告中的 `true_model_cold_start_controlled` 会保持 `false`；
- `warm_serial_full_response`：预热后的串行完整 JSON 回复，覆盖语义 guardrail；
- `warm_serial_streaming`：预热后的串行流式回复，同时报告模型首 token、用户可见首句和完成时间；
- `concurrent_full_response`：并发完整回复，单独统计本地 Qwen 与远端 API 的 semaphore 排队。

每个场景还输出 `flag_rates`，用于识别并发时“看似更快、实际已 fallback”的假象，包括
Qwen routing 使用率/回退率、generation LLM 使用率/回退率、Tool 实际调用率，以及 semantic
guardrail、retry、repair 的触发率。

细分阶段还包括 permission、active memory、主生成 Tool Calling 模型回合、Tool 执行、API generation、
semantic guardrail、repair generation 和 repair recheck。Repair 是条件路径：常规安全输出中
没有触发时，报告不会伪造一个 0 ms 均值，而是以缺失阶段/`sample_count=0` 表示，需用专门的
repair case 评测。

父子层级是 inclusive timing，不能把所有行直接相加。评测每次使用新建的 demo conversation，
避免历史长度增长混入当前基线；它覆盖应用服务和真实持久化，但不包含公网传输、反向代理、
认证中间件与客户端渲染。这些 HTTP 外围开销应与 `X-Process-Time` 和客户端首包时间对照。

运行本地 Qwen 意图路由质量与延迟评测：

```bash
make eval-local-intent
```

2026-08-15 在 `qwen3.5:4b`、Ollama、100% GPU、108 条串行样本上的一次 warm 对照如下。
严格 Schema 版本使用 Pydantic 生成的 JSON Schema、`reasoning_effort=none` 和当时的
384-token 输出上限；该表是开发机单次对照，不是生产 SLO：

| Metric | JSON object 基线 | Strict Schema | Dynamic compact Schema |
|---|---:|---:|---:|
| JSON valid rate | 100.0% | 100.0% | 100.0% |
| Schema valid rate | 75.0% | 100.0% | 100.0% |
| Routed accuracy | 74.1% | 96.3% | 97.2% |
| Routed macro F1 | 82.2% | 96.3% | 97.3% |
| Specialized false-positive rate | 0.0% | 0.0% | 0.0% |
| First request | 2.411 s | 2.226 s | 2.002 s |
| Average | - | 2.371 s | 1.613 s |
| Warm p50 | 1.935 s | 1.963 s | 1.585 s |
| Warm p95 | 4.642 s | 4.831 s | 1.715 s |
| Maximum | - | 5.292 s | 2.319 s |

Strict Schema 消除了 27 条 `INVALID_SCHEMA`，但没有消除尾延迟：新结果仍有 2/108 条超过
5 秒。Dynamic compact Schema 对普通单动作请求禁止生成无效 secondary action，并把说明字段
约束为不含用户内容的服务器固定短语；只有同时出现两类动作信号和明确顺序连接词时才开放
secondary action。它将超过 5 秒的样本降至 0/108，同时保留明确双动作的顺序计划能力。

### 2026-08-16 会话分阶段延迟

开发机配置为常驻 `llama-server` 上的 `qwen3.5:4b`、远端 `deepseek-chat`、PostgreSQL，
本地/远端并发上限分别为 2/4。普通支持样本为 synthetic demo，所有 warm 串行样本的
`routing_llm_used`、`generation_llm_used` 和完整回复的 `guardrail_semantic_checked` 都是 100%。

| Scenario | N | Total p50 | Total p95 | First output p50 | First output p95 |
|---|---:|---:|---:|---:|---:|
| first process request | 1 | 8.592 s | 8.592 s | 8.592 s | 8.592 s |
| warm serial full response | 20 | 8.621 s | 9.396 s | 8.620 s | 9.396 s |
| warm serial streaming | 10 | 8.180 s | 8.831 s | 5.993 s | 6.428 s |

`first process request` 只代表 benchmark 进程首请求；模型服务在运行前已经加载，因此
`true_model_cold_start_controlled=false`，不能将 8.592 秒称为真实模型冷启动。

Warm 串行完整回复的叶子阶段如下。阶段位于不同父级时可能是 inclusive timing，不应将文档中
所有阶段机械相加：

| Stage | Average | p50 | p95 |
|---|---:|---:|---:|
| risk identification | 0.735 s | 0.737 s | 0.827 s |
| intent routing (Qwen) | 2.190 s | 2.188 s | 2.259 s |
| permission decision | 0.002 ms | 0.002 ms | 0.002 ms |
| active memory | 4.8 ms | 4.9 ms | 5.4 ms |
| bounded context | 9.3 ms | 8.9 ms | 11.3 ms |
| Memory Tool decision model | 2.510 s | 2.442 s | 2.974 s |
| API response generation | 2.569 s | 2.612 s | 2.838 s |
| semantic output guardrail | 0.592 s | 0.567 s | 0.913 s |
| response persistence | 5.6 ms | 5.5 ms | 6.7 ms |
| response assembly | 5.5 ms | 5.1 ms | 6.5 ms |

流式场景的 request-relative model first-token p50/p95 为 5.802/6.170 秒，用户可见首句为
5.993/6.428 秒。首 token 之前已经串行执行 risk、routing 和 Memory Tool decision，因此这不是
远端生成模型自身的 TTFT。流式输出只执行逐句 deterministic guardrail；本组
`guardrail_semantic_checked` 未触发，不能与完整回复的 semantic guardrail 安全覆盖等同。

生产并发配置（4 并发、2 轮）得到 total p50/p95 7.231/12.283 秒，但
`routing_fallback=75%`，是 Qwen 与规则 fallback 的混合结果，不能作为全模型 p95。将评测进程的
本地等待和 HTTP timeout 临时提高到 20 秒后，Qwen 使用率达到 100%，total p50/p95 上升到
13.117/18.066 秒；本地 queue wait p95 为 8.282 秒，routing p95 为 11.485 秒。该次仅凭排队
现象推断为单 slot，后续 `/slots` 检查证明此推断不正确：服务实际有 4 个 slot，长排队来自
应用进程仍限制为 2 并发且等待参数与实验配置不一致。该结果仍能说明放宽 timeout 会隐藏容量
配置错误，但不能用来判断 llama-server 的真实 slot 数。

条件路径单独测量，不混入普通请求 p95：

- Memory Tool 5/5 发起真实 PostgreSQL 空结果查询：模型决策/continuation 平均 2.448 秒，
  Tool 执行平均 8.2 ms；
- 自然 semantic guardrail 对 invented-user-fact 样本 5/5 返回 allow，repair 未触发；
- 注入标准 violation 后，真实 repair generation 平均 0.599 秒、repair recheck 平均 0.566 秒，
  5/5 repair 成功，条件路径总成本平均 1.165 秒；
- 正式 Output Guardrail LLM eval 未通过：hard-safety containment 86.96%（gate 90%）、semantic
  detection recall 61.90%、soft invented-fact detection 0%、natural repair trigger 0%。因此
  forced-trigger repair 数字只能说明触发后的延迟，不能说明生产 repair 当前有效。

### 2026-08-20 主生成集成 Tool Calling 后复测

保持 `qwen3.5:4b`、`deepseek-chat`、PostgreSQL、本地并发上限 2、远端并发上限 4 不变。
普通支持样本中主生成模型每次都收到两个 Memory Tool 定义，但 20/20 完整回复和 10/10
流式回复都直接回答，`tool_called=0`。因此 `generation` 与 `tool_model` 是同一个统一主生成
循环的父子计时，不能相加；后者不再表示额外的 Memory Tool 预检请求。

| Scenario | N | Total p50 | Total p95 | First output p50 | First output p95 |
|---|---:|---:|---:|---:|---:|
| first process request | 1 | 6.364 s | 6.364 s | 6.364 s | 6.364 s |
| warm serial full response | 20 | 5.617 s | 6.028 s | 5.617 s | 6.028 s |
| warm serial streaming | 10 | 4.930 s | 5.533 s | 3.168 s | 3.446 s |
| concurrent full response | 8 | 5.223 s | 9.431 s | 5.223 s | 9.431 s |

与 2026-08-16 基线相比，warm 完整回复 total p50/p95 分别降低 3.004/3.368 秒，流式
首句 p50/p95 分别降低 2.825/2.982 秒。流式 request-relative model first-token p50/p95
由 5.802/6.170 秒降至 2.909/3.197 秒；补齐埋点后的远端流式 `generation` p50/p95 为
2.531/3.065 秒。

Warm 串行完整回复的主要阶段如下：

| Stage | Average | p50 | p95 |
|---|---:|---:|---:|
| risk identification | 0.855 s | 0.881 s | 1.062 s |
| intent routing (Qwen) | 1.431 s | 1.432 s | 1.557 s |
| unified API generation / Tool choice | 2.611 s | 2.572 s | 3.098 s |
| semantic output guardrail | 0.674 s | 0.681 s | 0.798 s |
| active memory | 4.6 ms | 4.5 ms | 5.0 ms |
| bounded context | 8.5 ms | 8.1 ms | 9.8 ms |

明确要求读取历史的条件路径另测 5 次，5/5 调用 `search_practice_memory`、5/5 无 fallback：
总耗时 p50/p95 为 7.525/7.991 秒，统一生成循环为 4.051/4.596 秒，实际 Tool 执行为
4.8/6.9 ms。该路径包含工具选择和工具结果后的第二个模型回合，不混入普通消息 p95。

并发结果仍是混合值：`routing_llm_used=50%`、`routing_fallback=50%`，因此 9.431 秒不能
作为全模型并发 p95。`true_model_cold_start_controlled=false`，首次进程请求也不能冒充真实
模型冷启动。完整原始报告位于开发机
`/tmp/socialease-conversation-latency-after-integrated-tools.json`，流式埋点补测位于
`/tmp/socialease-conversation-latency-streaming-instrumented.json`。

### 2026-08-20 llama-server 4-slot 与入口背压验收

通过鉴权 `/slots` 和 `/props` 实测当前 llama-server 有 4 个 slot，每个 slot 的 context 为
4096。应用侧改为 `LOCAL_LLM_MAX_CONCURRENCY=4`，独立 queue wait 为 5 秒，并限制最多再排队
4 个请求；模型 HTTP timeout 未提高。启动脚本显式传入 `--parallel 4 --ctx-size 4096`，避免
依赖 llama-server 的 auto slot 默认值。

验收运行 5 轮、每轮 4 个重叠请求，共 20 个并发样本：

| Metric | 修复前（8 samples） | 修复后（20 samples） |
|---|---:|---:|
| routing LLM used | 50% | 100% |
| routing fallback | 50% | 0% |
| local queue wait p95 | 251.879 ms | 0.032 ms |
| intent routing p95 | 5.034 s（混合 fallback） | 3.537 s（全 Qwen） |
| total p50 | 5.223 s | 7.401 s |
| total p95 | 9.431 s | 8.106 s |

修复后 4 路请求不再在应用入口被错误拒绝。全 Qwen 并行时，单个路由请求 p95 高于串行的
1.558 秒，是 4 个 slot 共享同一 GPU 算力后的吞吐/单请求延迟权衡，不是 queue wait。报告位于
`/tmp/socialease-conversation-latency-parallel4.json`。该进程运行前模型已经常驻，仍不属于受控
冷启动评测。

### 2026-08-20 Ollama 驻留、readiness 与单-slot 容量验收

使用 Ollama 0.32.13 在独立端口和临时模型目录进行验收。启动参数设置
`OLLAMA_NUM_PARALLEL=4`，但 Ollama 对 Qwen 3.5 明确报告该架构暂不支持 parallel requests，
内部 llama-server 实际以 `-np 1` 启动。因此 Ollama 路径的有效容量修正为 1 个执行 slot，
并由应用保留最多 4 个有界等待请求；这与直接使用 llama-server 的 4-slot 路径不同。

受控卸载后的首次最小推理和驻留确认耗时 4.195 秒；模型驻留后再次执行完整推理级启动探针
耗时 0.727 秒。readiness 同时确认 `resident=true`、`inference_verified=true`、
`slot_contract_ok=true`，容量快照为 1 个可执行 slot 和 4 个队列位置。

按完整对话评测口径运行 20 个串行、10 个流式及 2 轮 4 并发请求：

| Metric | Result |
|---|---:|
| warm serial total p50 / p95 | 4.030 s / 4.428 s |
| streaming first output p50 / p95 | 2.227 s / 2.497 s |
| concurrent total p50 / p95 | 4.841 s / 6.609 s |
| concurrent local queue wait p50 / p95 | 0.868 s / 2.113 s |
| concurrent routing LLM used / fallback | 100% / 0% |

4 并发下所有本地路由仍在 5 秒应用队列期限内获得 slot，没有进入 Ollama 的不可观测内部排队，
也没有 fallback。有限队列满和等待超时的拒绝/释放行为由确定性并发测试覆盖。完整报告位于
`/tmp/socialease-conversation-latency-ollama-capacity.json`；首次进程请求发生在模型已被生命周期
探针预热之后，因此没有标为受控模型冷启动。

最终运行时选择切回 llama-server 4 slots。Ollama 验收实现没有删除，而是扩展为统一的本地
模型生命周期：llama-server 同样执行启动真推理预热和后台恢复；readiness 通过 `/health` 与
`/slots` 读取实际容量，并要求其和应用 `LOCAL_LLM_MAX_CONCURRENCY=4` 一致。

同口径重跑 20 个串行、10 个流式及 2 轮 4 并发请求后，相对于已经启用 Safety/Intent 并行的
旧 4-slot 基线：串行路由平均从 843 ms 降到 639 ms，并发路由 p95 从 2.193 秒降到
1.666 秒；4 并发总耗时 p50/p95 从 4.573/5.250 秒降到 3.841/4.773 秒，本地 queue wait
p95 仍低于 0.1 ms，路由 LLM 使用率 100%、fallback 0%。串行总耗时 p50 从 3.485 秒降到
3.366 秒，但 p95 从 4.028 秒升到 4.425 秒，主要来自远端 generation 和 semantic guardrail
波动，因此不能归因于本地运行时。完整报告位于
`/tmp/socialease-conversation-latency-llama4-lifecycle.json`。

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

当前基线：

```text
backend pytest: 以当前 CI 为准
deterministic eval suite: all metrics passed
eval gate: passed
deterministic eval trace cases: 312 / 312 passed（2026-09-12 本地重跑）
real-LLM Output Guardrail quality: failed on 2026-08-16 (DeepSeek)
PostgreSQL migration/runtime CI: passed
Redis-backed task state CI: passed
```

最新结果以当前 CI 和生成的 `latest.json` 为准。

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
| deterministic workflow contract pass rate | 5 / 5 | 1.000 |
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

### 规则级 Workflow Contract

这 5 条在 eval runner 内组合规则 Safety、Router 与 Permission 的输出，不执行完整 Harness，
因此不再称为 E2E。真实应用入口回归位于 `test_application_entry_runs_real_conversation_service_and_harness`：
它在隔离 PostgreSQL 上执行 ConversationService → AgentHarness → 受控 Skill → Output Guardrail →
Trace/持久化路径，模型/Skill 输出使用确定性替身。

Output Guardrail 的确定性 suite 同样是标签驱动的 semantic client 回放，价值是验证 policy、
repair/recheck 状态机与指标计算，不代表任一真实模型的检测质量。真实模型质量由
`make eval-output-guardrail` 单独评测；2026-08-16 的 DeepSeek 结果仍是历史失败证据，本轮未调用
收费模型重测，不能据此宣称已经恢复。

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
