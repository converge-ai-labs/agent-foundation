---
title: Human-in-the-loop tools
sidebarTitle: Human-in-the-loop
description: Render built-in structured questions, define application-owned payload types, and resume with correlated answers.
---

Use `ask_user_question` to pause an Agent for a person's choice, then continue with the answer. This page walks through that round trip without a provider account. Your application supplies the form and stores the pending work; Harness supplies the schema and validates the correlated answer.

The lifecycle is: run → suspended result → collect an answer → fresh Run. Waiting does not keep a Harness Run open.

## Enable structured questions

Use the Python 3.13 environment from [Getting started](getting-started.md). Save this complete program as `question_demo.py`. The scripted Model asks one question; the application simulates selecting **Focused**.

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

Run `python question_demo.py`. Expected output:

```text
suspended Which scope should be used?
Proceeding with the focused scope.
```

`UserInteractionCapability` exposes the tool. The first Run returns its state and pending envelope. `build_results()` associates the answer with the actual call ID; `DeferredToolResume` carries that feedback into a fresh Run.

Replace the simulated `answer` assignment with your form, CLI prompt, or other human input. Keep the pending envelope and state while waiting. With a real Model, handle ordinary completed answers as well as suspension; asking a question is the Model's choice.

`RunBindings.deferred_tools_supported=False` omits the tool and its guidance. [Built-in inline children](delegation-and-codeact.md#host-managed-feedback) use that setting; a supporting Host can resume its own child.

Questions use pending `calls`, while tool approvals use `approvals`. Submit question answers as call results. An answer does not approve another tool; see [Managed tools](managed-tools.md) for approval policy and [Client-side tools](client-tools.md) for other external actions.

## Request payload

The arguments of one `ask_user_question` call are a JSON object containing `questions`. One call can ask several questions:

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

| Field                   | JSON type                 | Requirement                                            |
| ----------------------- | ------------------------- | ------------------------------------------------------ |
| `questions`             | Array of question objects | Required; 1–4 questions                                |
| `question`              | String                    | Required; 1–8,192 characters; unique within the call   |
| `header`                | String                    | Required; 1–12 characters                              |
| `options`               | Array of option objects   | Required; 2–4 options per question                     |
| `options[].label`       | String                    | Required; 1–256 characters; unique within the question |
| `options[].description` | String                    | Required; 1–4,096 characters                           |
| `multiSelect`           | Boolean                   | Optional; defaults to `false`                          |

The built-in models reject extra fields and strip surrounding whitespace from strings. Length and uniqueness checks apply after normalization; blank strings are invalid. Use the normalized question text and option labels when collecting answers. The JSON field is **`multiSelect`**, not the Python model's `multi_select` attribute. Python serialization uses `by_alias=True` to preserve that wire name.

Harness validates the full request before external deferral. Invalid questions, including duplicate texts or option labels, produce a tool failure for the model to correct rather than a pending form for the Host.

## Answer payload

Return a JSON object with `answers`, an optional general `response`, or both. The `answers` keys are the exact normalized **question texts**, not headers, array indices, option descriptions, or tool call IDs:

```json
{
  "answers": {
    "Which scope should be used?": "Focused",
    "Which outputs do you need?": ["Code", "Docs"]
  }
}
```

A value can be one string or an array of strings. Selected options use their **labels**. Free text is also allowed, for example:

```json
{
  "answers": {
    "Which scope should be used?": "Only update the Python integration.",
    "Which outputs do you need?": "A runnable example is enough."
  }
}
```

A general response can answer the call without a per-question mapping:

```json
{
  "response": "Keep the implementation focused and include a short guide."
}
```

| Field             | JSON type                                                | Requirement                                                                                |
| ----------------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `answers`         | Object mapping question text to a string or string array | Optional; defaults to `{}`; at most 4 entries                                              |
| Each answer key   | String                                                   | Non-blank; at most 8,192 characters                                                        |
| Each answer value | String or array of strings                               | Non-blank strings, at most 8,192 characters each; arrays contain 1–4 values                |
| `response`        | String or `null`                                         | Optional; defaults to `null`; a supplied string is non-blank and at most 65,536 characters |

Answer strings are whitespace-normalized, and extra fields are rejected. Harness checks the answer against the **exact pending request**:

- Every answer key must name a question from that call.
- Cover every question unless a non-null general `response` is supplied. A response can accompany a partial mapping, but it does not excuse invalid mapped answers.
- Do not mix selected option labels and free-text values within one answer array.
- If an answer selects option labels and `multiSelect` is `false`, select exactly one label. Free text is not restricted to the option list.

`{}` is not a successful answer to an unanswered call. If the user declines or the interaction times out, return an explicit failure rather than inventing an empty answer: an embedded Host supplies `ToolFailed`, and a Service client supplies a `failed` call result.

## Define types in your application

Service exposes tool arguments and returned values as generic JSON. Define the types needed by your application rather than assuming a Service SDK exports built-in question types. For example, these TypeScript types describe the wire shape only; they are **application-owned**, not SDK imports:

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

Static types do not check runtime JSON, length bounds, uniqueness, or answer/request correlation. Parse untrusted payloads and enforce the documented constraints at your application boundary; Harness's resume validation remains authoritative. Keep unknown client-tool payloads as JSON and interpret only the tool definitions your application actually supports, rather than casting every deferred call to a question.

Python applications embedding Harness can reuse its existing models. With `request_json` and `answer_json` containing the JSON strings above:

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

`UserQuestionAnswers.model_validate_json()` checks the answer model, not whether it answers this particular request. Correlation and semantic checks also run when Harness accepts the deferred resume. The [source models](https://github.com/converge-ai-labs/agent-foundation/blob/main/packages/a13n-harness/a13n_harness/toolsets/interaction.py) own the current fields and validators; match them to the Harness version used by your Host.

## Resume an embedded Harness Run

The complete program demonstrates the two-Run path. In a durable application:

1. On `status="suspended"`, retain the selected `state` and exact `deferred` envelope together.
2. Render each supported pending call and collect its answer. Keep each call's ID separate from its question texts.
3. Build results for every pending call and approval, each exactly once in its original category.
4. Start a new Run with fresh bindings, the saved state, and `DeferredToolResume(requests, results)`.
5. Handle the new result. It may complete, fail, be cancelled, or suspend with another batch.

The outer `calls` mapping uses `tool_call_id`; a question answer's inner `answers` mapping uses normalized question text. `approve_all=True` answers approvals, not questions.

For restart persistence and recovery after an interrupted resume, follow [State and resume](state-and-resume.md#structured-suspension). Keep accepted feedback paired with its current checkpoint as that guide describes.

## Answer through Service

A Service question appears in the waiting Run's `pending.calls`. Submit the structured answer as the `value` of a `returned` call result to `POST /api/v1/runs/{run_id}/resume`, using that exact waiting Run and call ID. For a batch with only this question call, the body is:

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

Replace `question-1` with the actual `tool_call_id`. Both maps are required and must cover the complete pending batch; use an empty map only for an empty category. An ordinary Thread message cannot resolve the wait, and optional resume `input` is additional context, not a replacement for answers or approval decisions. Service resumes into a distinct successor Run; the original Run keeps its sealed `waiting` status and cannot be resumed again.

Send the request with the Service's authentication, workspace selection, and a stable `Idempotency-Key`. See [Service waiting Runs](../a13n-service/agents-and-runs.md#resume-a-waiting-run) and the [SDK guides](../a13n-service/sdks.md) for complete submission workflows. Harness UI owns its own interactive forms and Host lifecycle; these wire examples do not prescribe its UI.
