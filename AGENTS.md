# SocialEase Agent 开发协作说明

## 1. 项目简介

SocialEase Agent 是面向大学生社交压力场景的安全可控 Agent 系统。它不是医疗产品，不做诊断，不替代心理咨询，不承诺治疗效果。

系统目标是通过工程化 agent workflow 提供：

- 社交情境模拟；
- CBT 风格结构化自助练习；
- 分级暴露训练；
- 校园支持资源导航；
- 高风险表达识别与 crisis escalation。

所有功能都应服务于安全、可控、可解释的演示型全栈项目，而不是简单的“心理聊天机器人”。

## 2. 安全边界

- 禁止输出诊断结论，例如“你患有某疾病”。
- 禁止承诺治疗效果，例如“这样一定会治好”。
- 禁止鼓励用户远离现实支持。
- 遇到自伤、自杀、伤害他人、严重危机表达时，必须进入 crisis escalation。
- Crisis response 应建议用户联系可信任的人、学校心理中心或当地紧急服务。
- 不要生成可能伤害用户的建议。
- 心理健康相关输出必须保持非医疗化：可以做情境拆解、表达练习、资源导航和自助练习，但不能替代专业服务。
- 不要硬编码真实学校电话或不存在的资源。
- Sample data 必须标注为 demo。

## 3. 工程规范

- Python 使用类型注解。
- FastAPI routes 保持轻量，业务逻辑放在 `services` / `agents` / `workflow` / `safety` 等模块。
- 所有 classifier 和 router 必须有单元测试。
- 所有 API 返回结构必须使用 Pydantic model。
- 每个新模块需要包含简短 docstring。
- 小步实现，避免一次性重构整个项目。
- 代码优先遵循当前目录和命名风格，新增抽象必须服务于真实复杂度。
- 新增心理健康相关文本时，必须检查是否违反安全边界。

## 4. 推荐目录结构

```text
backend/
  app/
    api/
    agents/
    workflow/
    safety/
    memory/
    tracing/
    evals/
  tests/
frontend/
  app/
```

## 5. 运行命令

Backend:

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Tests:

```bash
cd backend
pytest
```

## 6. Codex 工作方式

- 修改代码前先总结计划。
- 优先小步提交和小步验证。
- 每次修改后说明修改文件、运行方式、测试方式和当前 TODO。
- 不要一次性重构整个项目。
- 如果测试失败，先修测试。
- 任何心理健康相关输出都必须保持非医疗化和安全。
- 默认从已有代码结构出发；需求不清楚时做合理默认，并在结果中说明。

