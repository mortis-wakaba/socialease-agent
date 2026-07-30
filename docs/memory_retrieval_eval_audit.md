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

## 当前仍未覆盖

- 真正共享数据库/向量索引的多租户规模测试；
- 单用户几十到几百个 practice thread 的长历史；
- inactive/superseded 和索引删除一致性矩阵；
- 独立标注者创建、开发者不可见的 sealed test；
- PostgreSQL adapter 与真实模型联合运行的端到端延迟。

这些缺口不能用当前离线 JSONL 分数替代，应在任何向量索引上线前单独建立 Gate。
