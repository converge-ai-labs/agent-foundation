---
title: 人机协作工具
sidebarTitle: 人机协作
description: 使用 ask_user_question 暂停 Python 智能体执行，在应用中渲染结构化选项表单，并携带关联答案安全恢复运行。
---

`UserInteractionCapability` 为智能体提供 `ask_user_question` 工具，用于在需要人工选择时将当前 Run 以 `status == "suspended"` 干净挂起，并在收集用户回复后通过 `DeferredToolResume` 在新 Run 中继续执行。

## 使用 `ask_user_question` 暂停与恢复

保存为 `question_demo.py` 并运行 `uv run python question_demo.py`：

```python title="question_demo.py"
import asyncio
import json
from a13n_harness import AgentSpec, DeferredToolResume, HarnessBuilder, RunBindings
from a13n_harness.capabilities import AskUserQuestionRequest, UserInteractionCapability
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

question = {
    "questions": [{
        "question": "Which scope should be used?",
        "header": "Scope",
        "options": [
            {"label": "Focused", "description": "Change only the selected component."},
            {"label": "Broad", "description": "Change the whole repository."},
        ],
        "multiSelect": False,
    }]
}


async def respond(messages, info):
    returns = [
        p for m in messages if isinstance(m, ModelRequest)
        for p in m.parts if isinstance(p, ToolReturnPart)
    ]
    if not returns:
        yield {0: DeltaToolCall(name="ask_user_question", json_args=json.dumps(question), tool_call_id="q-1")}
    else:
        yield "Proceeding with the focused scope."


executable = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=FunctionModel(stream_function=respond),
    capabilities=(UserInteractionCapability(),),
)


async def main() -> None:
    first = await executable.run("Clarify the scope.", bindings=RunBindings.embedded())
    assert first.status == "suspended" and first.state is not None and first.deferred is not None
    call = first.deferred.calls[0].target_call
    pending = AskUserQuestionRequest.model_validate(call.args_as_dict())

    answer = {"answers": {pending.questions[0].question: "Focused"}}
    second = await executable.run(
        bindings=RunBindings.embedded(),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(
            first.deferred,
            first.deferred.build_results(calls={call.tool_call_id: answer}),
        ),
    )
    print(second.output_or_raise())


asyncio.run(main())
```

当运行不支持暂停等待人工输入时（如[内置内联子智能体](delegation-and-codeact.md#host-managed-feedback)），设置 `RunBindings.deferred_tools_supported=False`。提问位于 `deferred.calls`，工具权限审批位于 `deferred.approvals`（见[受管工具](managed-tools.md)）。

## 请求与回答结构

### 请求（`AskUserQuestionRequest`）

| 字段          | 类型   | 约束                                                  |
| ------------- | ------ | ----------------------------------------------------- |
| `questions`   | 数组   | 1–4 个问题对象                                        |
| `question`    | 字符串 | 1–8,192 字符，单次调用内唯一                          |
| `header`      | 字符串 | 1–12 字符短标题                                       |
| `options`     | 数组   | 每个问题 2–4 个 `{label, description}` 选项           |
| `multiSelect` | 布尔值 | 可选（默认 `false`；Python 序列化需 `by_alias=True`） |

### 回答（`UserQuestionAnswers`）

`answers` 以每个问题归一化后的完整 `question` 字符串（对应 `questions[i].question`）为键，值为所选 `label`、标签数组（`multiSelect: true` 时）或自由文本，也可提供顶层 `response`：

```json
{
  "answers": {
    "Which scope should be used?": "Focused",
    "Which outputs do you need?": ["Code", "Docs"]
  }
}
```

若用户拒绝或超时，请返回明确的工具失败（嵌入式 Harness 传入 `ToolFailed`，Service 传入 `"status": "failed"`），让模型获知拒绝状态。

## 在 Service 中恢复等待中的运行

在 Service 中，携带 `Idempotency-Key` 调用 `POST /api/v1/runs/{run_id}/resume`，一次性覆盖该批次所有待处理的 `approvals` 与 `calls`：

```json
{
  "approvals": {},
  "calls": {
    "q-1": {
      "status": "returned",
      "value": {
        "answers": {
          "Which scope should be used?": "Focused"
        }
      }
    }
  }
}
```

完整客户端流程见 [Service 等待中的运行](../a13n-service/agents-and-runs.md#resume-a-waiting-run)与 [SDK 指南](../a13n-service/sdks.md)。
