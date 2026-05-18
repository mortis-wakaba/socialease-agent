# SocialEase Agent 知识库设计

## 目标

SocialEase Agent 的知识库不是单一的大杂烩，而是按用途拆分的可追溯内容层。这样可以同时满足：

- 对外部权威知识的引用；
- 对项目自身行为规则的约束；
- 对大学生社交场景的产品化表达；
- 对未来真实校园资源导入的扩展。

## 总体分层

| 知识库 | 主要内容 | 主要用途 | 是否可直接面向用户回答 |
| --- | --- | --- | --- |
| `social_skills` | 社交焦虑科普、CBT 自助技巧、暴露练习原则、大学生社交场景脚本、练习任务阶梯 | 支撑 role-play、worksheet、exposure | 可以，但必须保持非医疗化 |
| `support_resources` | 真实、公开、可验证的支持资源说明 | 支撑资源导航与求助信息查询 | 可以，必须有 citations |
| `safety_policy` | 项目安全策略、危机升级规则、数字心理健康边界、隐私与安全原则 | 决定系统边界与输出约束 | 主要用于内部约束，也可解释边界 |
| `product_rubrics` | roleplay 反馈标准、worksheet 抽取规则、exposure ladder 设计规范、eval rubric | 约束产品内部行为与评测 | 默认不作为普通用户问答知识 |
| `campus_resources_demo` | 合成的校园资源样例 | 演示未来 verified campus import 的数据形态 | 可以，但必须明确标注 demo |

## 外部来源规划

### `social_skills`

建议纳入：

- **NIMH**：社交焦虑科普、CBT、暴露练习；
- **NHS / NHS Inform**：CBT 自助技巧、社交焦虑自助指南；
- **CCI**：社交焦虑模块化自助内容与 worksheet 结构参考。

使用方式：

- NIMH 主要作为背景知识与术语依据；
- NHS / NHS Inform 主要作为自助练习与非医疗化表达参考；
- CCI 主要作为模块结构和练习设计参考，不直接复刻 worksheet 原文。

### `safety_policy`

建议纳入：

- **WHO**：数字心理健康、心理健康促进、现实支持衔接等边界；
- **American Psychiatric Association / APA**：心理健康 app、AI 工具的安全、隐私、透明度与证据要求；
- **项目自有安全策略**：crisis escalation、禁止诊断、禁止治疗承诺、数据最小化。

使用方式：

- 外部来源提供原则依据；
- 项目自有策略负责把原则变成可执行规则；
- crisis 相关查询必须优先命中安全策略，而不是进入普通练习逻辑。

### `support_resources`

建议纳入：

- 真实、公开、可验证的支持资源说明；
- 适合长期保留的官方页面摘要；
- 后续若接入具体学校资源，只允许导入经过审核的正式资料。

使用方式：

- 当前版本不伪造具体学校电话、地址或联系人；
- unknown 时明确返回当前资源库没有足够信息；
- 未来如果部署到具体学校，再扩展 campus-specific 数据。

## 项目自有内容规划

### 放入 `social_skills`

- 大学生社交场景脚本：课堂发言、小组讨论、宿舍沟通、社团破冰、邀请同学吃饭、向老师提问等；
- 练习任务阶梯：从低难度到高难度的安全练习路径。

### 放入 `product_rubrics`

- roleplay 反馈标准：clarity、naturalness、assertiveness、empathy；
- worksheet extraction 规则；
- exposure ladder 设计规范；
- eval rubric 与通过标准。

### 放入 `safety_policy`

- crisis escalation；
- 非医疗化表达规范；
- 禁止输出清单；
- memory 数据最小化与隐私边界。

### demo 校园资源

保留，但严格标注为 demo：

- 只用于演示未来 campus-specific 导入后的数据形态；
- 不冒充真实学校服务；
- 不参与“真实支持资源”能力的宣传；
- 建议后续放在独立的 `campus_resources_demo` 或等价目录中。

## 推荐目录结构

```text
backend/data/knowledge_base/
  social_skills/
    external/
    project_authored/
  support_resources/
    external/
  safety_policy/
    external/
    project_authored/
  product_rubrics/
    project_authored/
  campus_resources_demo/
    project_authored/
```

说明：

- `external/` 表示来自外部公开来源的整理稿；
- `project_authored/` 表示项目团队自行编写的产品内容；
- 不同来源类型分目录，便于后续检索过滤、citation 展示和版权管理。

## 文档 frontmatter 规范

每篇 markdown 文档建议至少包含：

```yaml
---
title: "..."
source_name: "NIMH"
source_type: "external_public"
source_url: "https://..."
doc_type: "guide"
kb_type: "social_skills"
audience: "user_facing"
review_status: "reviewed"
last_reviewed: "2026-05-18"
---
```

字段说明：

| 字段 | 含义 |
| --- | --- |
| `title` | 文档标题 |
| `source_name` | 来源名称，例如 NIMH、NHS Inform、Project Authored |
| `source_type` | `external_public` / `project_authored` / `demo` |
| `source_url` | 外部来源链接；项目自有文档可留空或省略 |
| `doc_type` | 例如 `overview`、`guide`、`policy`、`rubric`、`scenario` |
| `kb_type` | 所属知识库 |
| `audience` | `user_facing` / `internal_only` |
| `review_status` | `draft` / `reviewed` / `deprecated` |
| `last_reviewed` | 最近审核日期 |

## 检索与回答规则

1. 用户问练习方法、场景准备、worksheet、exposure 时，优先检索 `social_skills`。
2. 用户问如何求助、有哪些支持资源时，优先检索 `support_resources`。
3. 用户出现危机表达时，直接进入 `safety_policy` + crisis escalation，不继续普通 RAG 问答。
4. 产品内部评分、抽取、eval 逻辑只读取 `product_rubrics`，默认不暴露给普通问答。
5. `campus_resources_demo` 只用于演示，不可和真实支持资源混称。
6. 没有命中足够证据时，返回 unknown，不补全联系方式、机构名称或治疗效果承诺。

## citation 展示建议

前端 citation 至少展示：

- 标题；
- 来源名称；
- 来源类型；
- 摘要片段；
- 对外部来源可展示原始链接；
- 对 demo 文档明确展示 `demo` 标识。

建议在 UI 中区分：

- `External public resource`
- `Project policy`
- `Project-authored practice content`
- `Demo resource`

## 当前实现顺序建议

1. 先把现有 `social_skills` 与 `safety_policy` 迁移到新的 frontmatter 规范；
2. 新增 `support_resources`，只放真实可核验的公开内容；
3. 再新增 `product_rubrics`，把当前散落在代码中的 rubric 文本整理出来；
4. 最后保留一个很小的 `campus_resources_demo`，仅用于展示未来导入形态。

## 明确不做的事

- 不把外部权威资料包装成项目自己的原创结论；
- 不把 demo 校园资源包装成真实资源；
- 不直接复制整套外部 worksheet 或长篇内容；
- 不让普通 RAG 输出覆盖 crisis escalation；
- 不把“支持资源查询”宣传成诊断、治疗或专业咨询替代品。
