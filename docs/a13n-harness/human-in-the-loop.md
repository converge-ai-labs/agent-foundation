---
title: Human-in-the-loop tools
sidebarTitle: Human-in-the-loop
description: Pause Python agent execution with ask_user_question, render structured choice forms in your application, and resume runs with correlated answers.
---

`UserInteractionCapability` gives an Agent the `ask_user_question` tool to pause execution for structured human choices, closing the active Run cleanly with `status == "suspended"` and resuming in a fresh Run via `DeferredToolResume`.

## Pause and resume with `ask_user_question`

Save as `question_demo.py` and run `uv run python question_demo.py`:

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

Set `RunBindings.deferred_tools_supported=False` when a Run cannot pause for human input (used by [built-in inline children](delegation-and-codeact.md#host-managed-feedback)). Questions appear in `deferred.calls`; tool permission prompts appear in `deferred.approvals` (see [Managed tools](managed-tools.md)).

## Request and answer schemas

### Request (`AskUserQuestionRequest`)

| Field         | Type    | Constraint                                            |
| ------------- | ------- | ----------------------------------------------------- |
| `questions`   | Array   | 1–4 question objects                                  |
| `question`    | String  | 1–8,192 chars, unique within the call                 |
| `header`      | String  | 1–12 chars                                            |
| `options`     | Array   | 2–4 `{label, description}` objects per question       |
| `multiSelect` | Boolean | Optional (`false` default; serialize `by_alias=True`) |

### Answer (`UserQuestionAnswers`)

Key `answers` by each question's exact normalized `question` string (matching `questions[i].question`), mapped to a selected option `label`, an array of labels (when `multiSelect` is `true`), or free-text string(s), and/or supply a top-level `response`:

```json
{
  "answers": {
    "Which scope should be used?": "Focused",
    "Which outputs do you need?": ["Code", "Docs"]
  }
}
```

If the user declines or times out, return an explicit tool failure (`ToolFailed` in embedded Harness, or `"status": "failed"` in Service) so the model observes the rejection.

## Resume a waiting Run in Service

In Service, submit the answer to `POST /api/v1/runs/{run_id}/resume` (with `Idempotency-Key`) covering all pending `approvals` and `calls`:

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

See [Service waiting Runs](../a13n-service/agents-and-runs.md#resume-a-waiting-run) and the [SDK guides](../a13n-service/sdks.md) for end-to-end client workflows.
