---
title: 人机协作工具
sidebarTitle: 人机协作
description: 展示内置的结构化问题、定义应用自己的载荷类型，并携带关联的答案恢复执行。
---

使用 `ask_user_question` 暂停 Agent，等待用户选择，再携带答案继续。本页用无需 provider 账号的示例演示完整流程。应用负责表单和待处理工作的保存；Harness 提供 schema，并校验关联的答案。

生命周期为：执行 → 返回挂起结果 → 收集答案 → 新 Run。等待期间不保持 Harness Run 打开。

## 启用结构化问题

使用[快速开始](getting-started.md)中准备的 Python 3.13 环境。将以下完整程序保存为 `question_demo.py`。脚本化 Model 提出一个问题，应用模拟选择 **Focused**。

```python title="question_demo.py"
import asyncio
import json
from collections.abc import AsyncIterator

from a13n_harness import AgentSpec, DeferredToolResume, HarnessBuilder, RunBindings
from a13n_harness.capabilities import AskUserQuestionRequest, UserInteractionCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

question = {
    "questions": [{
        "question": "Which scope should be used?",
        "header": "Scope",
        "options": [
            {"label": "Focused", "description": "Change the selected component."},
            {"label": "Broad", "description": "Change the whole repository."},
        ],
        "multiSelect": False,
    }]
}


async def respond(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str | DeltaToolCalls]:
    returns = [
        part
        for message in messages if isinstance(message, ModelRequest)
        for part in message.parts if isinstance(part, ToolReturnPart)
    ]
    if not returns:
        yield {0: DeltaToolCall(
            name="ask_user_question",
            json_args=json.dumps(question),
            tool_call_id="question-1",
        )}
    else:
        assert "Focused" in str(returns[-1].content)
        yield "Proceeding with the focused scope."


executable = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=FunctionModel(stream_function=respond),
    capabilities=(UserInteractionCapability(),),
)


async def main() -> None:
    first = await executable.run("Clarify the scope.", bindings=RunBindings.embedded())
    assert first.status == "suspended"
    assert first.state is not None and first.deferred is not None
    requests = first.deferred
    assert len(requests.calls) == 1 and not requests.approvals
    call = requests.calls[0].target_call
    assert call.tool_name == "ask_user_question"
    pending = AskUserQuestionRequest.model_validate(call.args_as_dict())
    print(first.status, pending.questions[0].question)

    # A real application displays the question and collects this answer from a person.
    answer = {"answers": {pending.questions[0].question: "Focused"}}
    second = await executable.run(
        bindings=RunBindings.embedded(),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(
            requests, requests.build_results(calls={call.tool_call_id: answer})
        ),
    )
    assert second.run_id != first.run_id
    print(second.output_or_raise())


asyncio.run(main())
```

运行 `python question_demo.py`。预期输出：

```text
suspended Which scope should be used?
Proceeding with the focused scope.
```

`UserInteractionCapability` 提供工具。第一个 Run 返回状态和待处理封装。`build_results()` 将答案与实际调用 ID 关联，`DeferredToolResume` 将反馈带入新的 Run。

将模拟的 `answer` 赋值替换为表单、命令行提示或其他真实用户输入。等待期间保留待处理封装和状态。使用真实 Model 时，同时处理普通完成和挂起结果；是否提问由 Model 决定。

`RunBindings.deferred_tools_supported=False` 会移除工具及其指导。[内置内联子 Agent](delegation-and-codeact.md#host-managed-feedback)使用这一设置；支持恢复的 Host 可以恢复自己管理的子 Agent。

问题放在待处理 `calls` 中，工具审批放在 `approvals` 中。将问题答案作为调用结果提交；答案不会批准其他工具。审批策略见[受管工具](managed-tools.md)，其他外部操作见[客户端工具](client-tools.md)。

## 请求载荷

一个 `ask_user_question` 调用的参数是包含 `questions` 的 JSON 对象。一次调用可以提出多个问题：

```json
{
  "questions": [
    {
      "question": "Which scope should be used?",
      "header": "Scope",
      "options": [
        {"label": "Focused", "description": "Change only the selected component."},
        {"label": "Broad", "description": "Apply the change across the repository."}
      ],
      "multiSelect": false
    },
    {
      "question": "Which outputs do you need?",
      "header": "Outputs",
      "options": [
        {"label": "Code", "description": "Include the implementation."},
        {"label": "Docs", "description": "Include the user guide."}
      ],
      "multiSelect": true
    }
  ]
}
```

| 字段                    | JSON 类型    | 要求                             |
| ----------------------- | ------------ | -------------------------------- |
| `questions`             | 问题对象数组 | 必填；1–4 个问题                 |
| `question`              | 字符串       | 必填；1–8,192 个字符；调用内唯一 |
| `header`                | 字符串       | 必填；1–12 个字符                |
| `options`               | 选项对象数组 | 必填；每个问题 2–4 个选项        |
| `options[].label`       | 字符串       | 必填；1–256 个字符；问题内唯一   |
| `options[].description` | 字符串       | 必填；1–4,096 个字符             |
| `multiSelect`           | 布尔值       | 可选；默认为 `false`             |

内置模型拒绝额外字段，并去除字符串首尾的空白。长度和唯一性检查在规范化后执行；空白字符串无效。收集答案时，使用规范化后的问题文本和选项标签。JSON 字段是 **`multiSelect`**，不是 Python 模型的 `multi_select` 属性。Python 序列化时使用 `by_alias=True` 保留协议字段名。

Harness 在外部延后之前校验完整请求。包括重复问题文本或选项标签在内的无效问题会产生工具失败，让模型修正，而不会成为等待 Host 展示的表单。

## 答案载荷

返回包含 `answers`、可选的通用 `response` 或二者的 JSON 对象。`answers` 的键是精确匹配的、规范化后的**问题文本**，不是标题、数组下标、选项描述或工具调用 ID：

```json
{
  "answers": {
    "Which scope should be used?": "Focused",
    "Which outputs do you need?": ["Code", "Docs"]
  }
}
```

值可以是一个字符串或字符串数组。选择选项时使用其**标签**。也可以提供自由文本，例如：

```json
{
  "answers": {
    "Which scope should be used?": "Only update the Python integration.",
    "Which outputs do you need?": "A runnable example is enough."
  }
}
```

通用回复可以回答整次调用，而不逐题映射：

```json
{
  "response": "Keep the implementation focused and include a short guide."
}
```

| 字段       | JSON 类型                                | 要求                                                            |
| ---------- | ---------------------------------------- | --------------------------------------------------------------- |
| `answers`  | 将问题文本映射到字符串或字符串数组的对象 | 可选；默认为 `{}`；最多 4 项                                    |
| 每个答案键 | 字符串                                   | 非空白；最多 8,192 个字符                                       |
| 每个答案值 | 字符串或字符串数组                       | 每个字符串非空白且最多 8,192 个字符；数组包含 1–4 个值          |
| `response` | 字符串或 `null`                          | 可选；默认为 `null`；提供字符串时必须非空白且最多 65,536 个字符 |

答案字符串会规范化首尾空白，额外字段会被拒绝。Harness 将答案与**精确的待处理请求**关联校验：

- 每个答案键必须对应本次调用中的问题。
- 必须覆盖每个问题，除非提供非 null 的通用 `response`。通用回复可以与部分映射一起提交，但不能使无效的映射答案变为有效。
- 同一个答案数组不能混合选项标签和自由文本。
- 如果答案选择选项标签，且 `multiSelect` 为 `false`，必须恰好选择一个标签。自由文本不受选项列表限制。

`{}` 不是对未回答调用的成功答案。如果用户拒绝回答或交互超时，应返回显式失败，不要伪造空答案：嵌入式 Host 提供 `ToolFailed`，Service 客户端提供 `failed` 调用结果。

## 在应用中定义类型

Service 将工具参数和返回值作为通用 JSON 暴露。为应用定义需要的类型，而不要假定 Service SDK 导出了内置问题类型。例如，下面的 TypeScript 类型只描述协议结构；它们**由应用维护**，不是 SDK 导入：

```typescript
export interface UserQuestionOption {
  label: string;
  description: string;
}

export interface UserQuestion {
  question: string;
  header: string;
  options: UserQuestionOption[];
  multiSelect?: boolean;
}

export interface AskUserQuestionRequest {
  questions: UserQuestion[];
}

export interface UserQuestionAnswers {
  answers?: Record<string, string | string[]>;
  response?: string | null;
}
```

静态类型不会校验运行时 JSON、长度限制、唯一性或答案与请求的关联。在应用边界解析不可信载荷，并落实本文约束；Harness 的恢复校验仍是最终依据。保留未知客户端工具载荷的 JSON 形式，只解释应用实际支持的工具定义，不要将每个延后调用都强制转换为问题。

嵌入 Harness 的 Python 应用可以复用已有模型。假设 `request_json` 和 `answer_json` 包含上面的 JSON 字符串：

```python
from a13n_harness.capabilities import (
    AskUserQuestionRequest,
    UserQuestion,
    UserQuestionAnswers,
    UserQuestionOption,
)

request = AskUserQuestionRequest.model_validate_json(request_json)
answer = UserQuestionAnswers.model_validate_json(answer_json)
request_payload = request.model_dump(mode="json", by_alias=True)
answer_payload = answer.model_dump(mode="json", exclude_none=True)
```

`UserQuestionAnswers.model_validate_json()` 只校验答案模型，不判断它是否回答了这份具体请求。Harness 接受延后恢复时，还会执行关联与语义检查。[源代码模型](https://github.com/converge-ai-labs/agent-foundation/blob/main/packages/a13n-harness/a13n_harness/toolsets/interaction.py)定义当前字段和校验器；请与 Host 使用的 Harness 版本保持一致。

## 恢复嵌入式 Harness Run

完整程序演示了两个 Run 之间的续接。在需要持久化的应用中：

1. 收到 `status="suspended"` 后，将选定的 `state` 和精确的 `deferred` 封装一起保存。
2. 展示每个受支持的待处理调用并收集答案。分别保存调用 ID 和问题文本。
3. 为每个待处理调用和审批构建结果，每项在原始类别中恰好出现一次。
4. 使用新绑定、保存的状态和 `DeferredToolResume(requests, results)` 启动新 Run。
5. 处理新结果：它可能完成、失败、取消，也可能携带新批次再次挂起。

外层 `calls` 映射使用 `tool_call_id`，问题答案的内层 `answers` 映射使用规范化后的问题文本。`approve_all=True` 处理审批，不回答问题。

重启持久化和恢复过程中断后的处理见[状态与恢复](state-and-resume.md#structured-suspension)。按该指南要求，将已接受的反馈与当前检查点配对保存。

## 通过 Service 回答

Service 问题出现在等待中 Run 的 `pending.calls` 中。使用精确的等待 Run 和调用 ID，将结构化答案作为 `returned` 调用结果的 `value`，提交到 `POST /api/v1/runs/{run_id}/resume`。仅含这个问题调用的批次使用以下请求体：

```json
{
  "approvals": {},
  "calls": {
    "question-1": {
      "status": "returned",
      "value": {
        "answers": {
          "Which scope should be used?": "Focused",
          "Which outputs do you need?": ["Code", "Docs"]
        }
      }
    }
  }
}
```

将 `question-1` 替换为实际的 `tool_call_id`。两个映射都必填，并且必须覆盖完整待处理批次；只有类别为空时才能使用空映射。普通 Thread 消息无法解除等待；可选的恢复 `input` 只是附加上下文，不替代答案或审批决定。Service 恢复会创建不同的后继 Run；原 Run 保持其已封存的 `waiting` 状态，不能再次恢复。

发送请求时，提供 Service 的认证、工作区选择和稳定的 `Idempotency-Key`。完整提交流程见[Service 等待 Run](../a13n-service/agents-and-runs.md#resume-a-waiting-run)和 [SDK 指南](../a13n-service/sdks.md)。Harness UI 管理自己的交互表单和 Host 生命周期；这些协议示例不规定其 UI。
