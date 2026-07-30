# 长期记忆检索评测链路审计

审计日期：2026-07-30

## 审计范围

审计覆盖 JSONL/seed 加载、规模语料生成、case corpus 组装、SQL/Vector/v2
策略执行、embedding/reranker 缓存、指标聚合和 adoption Gate。生产检索策略没有在
本次审计中切换，仍保持 SQL Text。

## 已确认并修复的问题

### 1. 规模背景污染功能和 held-out case

旧 evaluator 为每个 case 用户注入 2,048 条通用背景。这使跨用户、过期、归档、
PII、危机、注入和应拒答 case 在正确过滤目标后仍可能召回无标注背景，相关性失败
和硬过滤失败无法区分。

修复后：

- development 和 held-out case 只包含各自人工标注 fixture；
- 只有 scale split 包含 2,048 条背景；
- development 最大候选数为 5，held-out 为 3。

### 2. Scale query 没有共享同一长历史

旧 evaluator 的 36 个 scale query 分别只看到自己的 target/hard negative 和背景，
同一个模拟用户的其他人工记忆不可见。

修复后 36 个 scale query 共享同一 corpus：

- 2,048 条背景；
- 108 条人工 target/hard negative；
- 每个 scale query 共 2,156 个候选。

### 3. 指标口径因策略而异

旧 SQL/Vector 只把 `category=abstention` 纳入拒答指标，v2 使用所有
`expected_abstain=true` case，导致总数分别为 4 和 16。

修复后所有策略使用同一个指标聚合器，拒答指标分母均为 16。

### 4. Conflict 指标错误要求空结果

旧指标把所有 conflict case 定义为“必须不返回任何记录”，因此“返回当前新偏好且
排除旧偏好”也会失败。

修复后 conflict pass 同时要求：

- 所有 expected memory 已返回；
- forbidden memory 未返回；
- 只有显式 `expected_abstain=true` 时才要求空结果。

### 5. Development、scale、held-out 混合 Gate

旧 Gate 使用 75 条 case 的聚合报告，无法区分调试集收益、规模召回和 held-out 安全。

修复后报告分别输出：

- `development`：23 条；
- `scale`：36 条；
- `held_out`：16 条。

Gate 使用 scale split 判断 Recall@3 增益，使用 held-out split 判断安全和 case pass，
使用 aggregate full-pipeline 判断 p95 延迟。development 不参与最终 Gate。

当前 held-out 已被开发者查看和分析，应视为从本次修复后冻结的 validation split；
正式上线前仍需由独立标注者提供新的 sealed test split。

### 6. 延迟测量存在缓存顺序偏差

旧 Dense-only 承担首次全部文档 embedding 成本，后续策略复用文档和 query cache，
不同策略延迟不可直接比较。

修复后：

- 文档 embedding 在 query timer 外预构建并单独报告；
- 每个策略开始前清空 query cache；
- reranker 使用 demo 文本预热一次；
- warm query p95 不包含模型下载、文档建索引和首次 ONNX 图初始化。

### 7. 旧 Vector request 丢失结构化上下文

旧 Vector evaluator 没有把 `scenario_id`、`practice_thread_id` 和 `skill_codes` 放入
`MemoryRetrievalRequest`，与 SQL/v2 的作用域不一致。现已补齐。

### 8. 索引规模字段含义不准确

旧 `indexed_memory_count` 按 summary 去重，不能表示独立用户记录数。

修复后：

- `indexed_memory_count`：按 `(user_id, memory_id)` 统计，共 2,232；
- `unique_summary_count`：需要独立 embedding 的文本数，共 2,160；
- `max_candidates_per_query`：2,156。

### 9. 本地模型无法被完整复现

旧报告只记录 reranker 名称，Dense adapter 只能依赖 FastEmbed 隐式缓存；缓存清理后
会重新联网，且本地 ONNX 文件没有进入报告指纹。

修复后：

- embedding 和 reranker 都支持显式 `specific_model_path`；
- 本地目录在模型加载前检查必需文件和空文件；
- 报告保存两个 provider、模型名、ONNX SHA-256 和 embedding dimensions；
- 报告保存 candidate window、RRF、通道上限、融合权重、Abstention 阈值和预算。

离线复测使用：

```bash
SOCIALEASE_EMBEDDING_MODEL_PATH=/path/to/bge-small-zh-v1.5 \
SOCIALEASE_RERANKER_MODEL_PATH=/path/to/bge-reranker-base \
make eval-memory-ablation
```

## 新增防污染契约

- case 内 memory id 唯一；
- expected/forbidden id 必须存在于 fixture 且不能重叠；
- 每个 case 必须有 expected memory 或显式 expected abstention；
- development/scale/held-out 的 case id、query 和 fixture summary 精确不重叠；
- 非 scale case 禁止出现生成的 scale background；
- 所有 scale query 必须看到同一份共享 corpus；
- 文档只预计算一次，但相同 query 在各相关 ablation 中必须重新执行 embedding。

## 2026-07-30 二次审计修正

本轮复核发现，上一版报告仍不足以支持产品采用结论：

- scale 的 12 个 seed 曾为三个 paraphrase 分别复制 target 和 hard negative，
  108 条 fixture 实际只有 36 个可辨识记录，形成 36 个三元等价组和 72 条冗余；
- `sql_text` ablation 实际是计时器外截取最近 100 条，再执行 Python lexical
  ranking，不是 PostgreSQL FTS；
- 原 held-out 已被人工查看，现已降级为 validation。默认 sealed held-out 为空，
  没有显式注入独立 sealed 文件时 Adoption Gate 必须保持关闭；
- Hard Filter 曾把基于否定词和 lexical overlap 的语义冲突与 owner、lifecycle、
  expiry、privacy 等不可协商边界混在一起；
- false-recall avoidance 只有 query-level “是否碰到任一 forbidden”，不能描述
  返回列表中的逐项污染。

已实施的结构修正：

- 每个 scale seed 只创建一条 target 和一组 hard negatives，三个 paraphrase 共享
  相同持久 ID；
- scale corpus 混入三个外部 demo 用户的同文本背景，并复用 scenario/thread ID，
  以压力测试大规模 ownership hard filter；
- recent-window ablation 正名为 `sql_recent_window_100`；
- Repository Protocol 和 PostgreSQL adapter 新增真正的 `tsvector/tsquery` 文本
  召回契约，迁移 `0020_add_memory_fts_index` 增加 GIN 表达式索引。该实现尚未在
  隔离 PostgreSQL eval 数据库上完成中文配置、EXPLAIN 和端到端 latency 验证，
  因此还不能进入 Adoption Gate；
- 当前 query/summary 的启发式冲突不再执行 hard reject；确定的旧偏好由
  `superseded` 生命周期表达；
- Full Pipeline 最终选择增加逐候选上下文覆盖、相对 top 分差、候选间冲突排除和
  content-hash 去重；
- Recall@3 改为 query-average，并增加 Hit@3、all-relevant coverage 和
  forbidden-item avoidance；
- content-free outcome 增加 union/rerank expected rank 与阶段 latency；
- 报告增加 dataset manifest SHA-256。

## 当前仍未覆盖

- 真正共享数据库/向量索引的多租户规模测试；
- 单用户几十到几百个 practice thread 的长历史；
- inactive/superseded 和索引删除一致性矩阵；
- 独立标注者创建、开发者不可见的 sealed test；
- PostgreSQL adapter 与真实模型联合运行的端到端延迟。
- 隔离 PostgreSQL 数据库中的 FTS 与 Vector/Hybrid 公平 candidate-space 对比；
- 独立管理且尚未被开发者查看的 sealed held-out；
- 中文 PostgreSQL FTS analyzer/分词配置的质量验证；
- cluster-aware bootstrap 或按 seed 配对的置信区间。

这些缺口不能用当前离线 JSONL 分数替代，应在任何向量索引上线前单独建立 Gate。

## 2026-07-30 clean-v3 审计结论

### 当前数据流

```text
JSONL/scale seed
  -> split 校验与 case-scoped corpus 组装
  -> owner/status/type/expiry/privacy Hard Filter
  -> Dense/BM25/Metadata/Multi-Query 各通道 recall
  -> candidate union + RRF
  -> top-20 Cross-Encoder rerank
  -> 绝对分数、相对 top 分差、上下文、冲突、去重的逐候选选择
  -> query-level abstention
  -> top-3 / 256-token budget
  -> content-free outcome
  -> aggregate + development/scale/validation/sealed-held-out metrics
  -> sealed safety + scale recall + aggregate latency Adoption Gate
```

`sql_recent_window_100` 仍是最近窗口加 Python lexical ranking，只代表当前生产形状；
`postgres_fts` 是独立的 PostgreSQL `tsvector/tsquery` baseline。两者不再混称。

### 数据与运行身份

- sealed JSONL：96 case，SHA-256
  `4245392b03af45abba3e4036bd7ba11ce9bce7174b2d663c8ad9c017398a1700`；
- 完整 dataset manifest：
  `1acd33d531c84b463b38f4b27a82cd626ac4e6efe73f950191fd447bcd204809`；
- split：development 23、scale 36、validation 16、sealed-held-out 96；
- conceptual records 10,004，unique summaries 2,652，单 query 最大候选 8,228；
- 修正后报告：`/tmp/memory-ablation-result.clean-v3.json`；
- 原始、含旧指标聚合 bug 的报告：
  `/tmp/memory-ablation-result.clean-v3.raw-metric-bug.json`。

sealed 只用于本轮固定参数运行。随后发现的两个指标 bug 只从相同 outcomes
重新聚合，没有重跑模型、调整阈值或改变 retrieved IDs。

### 问题分级、影响和回归保护

| 级别 | 类型 | 证据与指标方向 | 可能的错误结论 | 修复 / 防回归 |
|---|---|---|---|---|
| P0 | 数据集 bug | 旧 scale 有 36 个三元等价组、72 个冗余副本；会挤占 top-3，并使 ID recall、MRR、false recall 任意偏高或偏低 | 把副本竞争误判成算法能力 | 每 seed 一条 target 和一组 negatives，三条 paraphrase 共享 ID；测试等价组和 shared corpus |
| P0 | 实验设计 | 已观察的 16 条 held-out 会产生隐式调参泄漏 | validation 上的改进被误称泛化 | 降级为 validation；新 96 条 sealed 只显式注入；无 sealed 时 Gate 强制关闭 |
| P0 | 指标 bug | sealed 类别名为 `conflict_or_supersession` / `ownership_or_lifecycle`，旧字面量匹配使 safety denominator=0 | 零覆盖被误当成安全通过或无法解释 | conflict 按预声明类别；lifecycle/owner 按 fixture status/expiry/owner；测试分母 12/64/84 |
| P0 | 指标 bug | 旧 `forbidden_item_avoidance` 实为 returned-judged precision | 将列表污染率和 label avoidance 混为一谈 | 分成 query-level false avoidance、forbidden-label avoidance、judged item precision；独立单测 |
| P1 | baseline bug | `sql_text` 是 recent 100 + Python lexical，不调用 PostgreSQL FTS；旧 scale target 被 2,048 条更新背景排除，Recall@3 固定为 0 | “PostgreSQL 文本检索无能力” | 正名 `sql_recent_window_100`；增加真实 `postgres_fts`；Gate 以 FTS 为比较 baseline |
| P1 | 性能工程 | 10,004 条语料 EXPLAIN 选择 `idx_episodic_memories_user_status`，未选择 FTS GIN；FTS 仍执行 `tsvector/tsquery` filter | 宣称已验证 GIN 或把 87.6ms P95 当作 GIN 性能 | 保留 content-free EXPLAIN 证据；不强制 planner、不跨 tenant 先检索；后续研究分区/RLS/索引设计 |
| P1 | latency 设计 | recent window 构造曾在 timer 外；缓存曾按策略顺序复用 | SQL 被低估、后跑策略被低估 | candidate assembly 纳入 timer；文档 embedding、FTS load/warmup、reranker warmup 单独报告；每 variant 清 query cache |
| P1 | 公平性 | FTS 包含真实 DB I/O，Vector/BM25 仍在内存 corpus 上执行 | 把延迟差异直接当产品架构差异 | 当前仅作质量候选空间比较；进入产品 Gate 前需同构 DB/index fetch benchmark |
| P1 | 实现 bug | query/summary 否定词与 lexical overlap 曾进入 Hard Filter，出现 `eligible_count=0` | 把启发式误判成不可协商安全边界 | Hard Filter 只保留 owner/lifecycle/type/expiry/privacy；supersession 用结构化 status；回归 preference 更新 |
| P1 | 实现 bug | top candidate 通过后，旧逻辑返回所有绝对分数达标项 | Recall 正确但 hard negative 同时返回 | 增加逐候选阈值、相对 margin、上下文、冲突排除、content-hash 去重；逐项回归测试 |
| P1 | 隔离覆盖 | 旧 scale background 只有一个用户 | 小 corpus cross-user pass 被误推到大规模 | scale 加三个外部 demo 用户、同文本和重复 scenario/thread ID；owner 在 recall 前 hard filter |
| P1 | 可复现性 | 模型路径、revision、参数或一次性成本曾缺失 | 无法复跑或比较 cold/warm | 校验本地文件，报告 provider/revision/dimensions、所有权重阈值、manifest 和 stage duration |
| P2 | 统计设计 | scale 只有 12 seed × 3 paraphrase；同 seed query 相关 | 把 36 query 当 36 个独立样本，夸大显著性 | 后续按 seed cluster bootstrap/paired CI；当前只报告点估计 |
| P2 | 诊断 | 旧报告不能区分 union、rerank、selection/abstention miss | 看到低分后直接调阈值 | outcome 增加 expected union/rerank rank、eligible/union/rerank counts 和阶段 latency |

### clean-v3 结果与失败位置

结果只支持“继续实验”，不支持产品采用：

- `adoption_gate_met=false`，生产选择保持 `sql_recent_window_100`；
- scale Recall@3：PostgreSQL FTS 19.4%，Full Pipeline 36.1%，增益 16.7
  个百分点；但 Full Pipeline scale false-recall avoidance 只有 55.6%；
- sealed Full Pipeline：Recall@3 86.3%，all-relevant coverage 83.3%，case pass
  75.0%，query-level false-recall avoidance 88.5%，judged item precision 88.2%，
  conflict resolution 91.7%，lifecycle 和 cross-user avoidance 均为 100%；
- sealed PostgreSQL FTS：Recall@3 88.1%，case pass 62.5%，conflict resolution
  0%，说明 lexical baseline 不能处理 supersession；
- aggregate Full Pipeline P95 865.3ms，超过 250ms Gate；其中 recall P95
  485.2ms、reranker P95 379.4ms；
- BM25 sealed Recall@3 92.9%、P95 0.134ms，但 false-recall avoidance 69.8%、
  conflict resolution 0%，不能因质量/延迟点估计而采用。

Full Pipeline 的 expected-case 失败归因：

| split | expected cases | union miss | rerank top-3 miss | selection/abstention miss | success |
|---|---:|---:|---:|---:|---:|
| development | 15 | 0 | 3 | 2 | 10 |
| scale | 36 | 12 | 9 | 2 | 13 |
| validation | 8 | 0 | 0 | 4 | 4 |
| sealed-held-out | 84 | 0 | 0 | 14 | 70 |

没有 expected case 因 Hard Filter 得到 `eligible_count=0`。scale 的主要问题在 union
和 rerank；sealed 的 14 个 relevant failure 全部发生在逐候选选择/abstention。
这只是失败定位，不授权根据 sealed 失败调阈值。

### Gate 明确失败原因

Full Pipeline 同时违反三个独立条件：

1. sealed query-level false-recall avoidance 88.5%，未达到 100%；
2. sealed conflict resolution 91.7%，未达到 100%；
3. aggregate P95 865.3ms，超过 250ms。

虽然 scale Recall@3 相对 PostgreSQL FTS 的增益超过 10 个百分点，且 sealed case
pass 高于 FTS，但不能抵消任何安全或延迟门禁。不得降低 Gate。

### 仍需后续验证

- development 调参后使用新的独立 validation 选择方案；当前 validation 可继续用，
  本轮 sealed 自此视为已打开，不得再次用于参数选择；
- 为下一轮最终判断生成新的 sealed v2，且在方案冻结前不向实现者暴露结果；
- 让 Vector/Hybrid 的候选 fetch、索引和数据库 I/O 与 PostgreSQL FTS 同构；
- 研究 PostgreSQL 在 tenant-first 条件下实际采用 GIN 的 schema/partition/RLS
  设计，不以 planner hint 制造“使用 GIN”的结果；
- 扩展 seed 数并按 seed 做配对置信区间；
- 只在 development 上研究 candidate calibration、MMR 和冲突结构化版本，不根据
  本轮 sealed 的 14 个 selection miss 或 1 个 conflict miss 调参。
