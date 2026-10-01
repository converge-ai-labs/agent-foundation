---
title: Human-in-the-loop tools
sidebarTitle: Human-in-the-loop
description: Render built-in structured questions, define application-owned payload types, and resume with correlated answers.
---

Use Harness's built-in `ask_user_question` tool when an Agent needs a person to choose options or supply clarification. Your application renders the question, collects the answer, and resumes the suspended work. Harness supplies the question format and validates the answer; it does not supply a form renderer or keep a Run open while waiting for a person.

Questions are external tool **calls**, not tool **approvals**. Both use deferred feedback, but their payloads and authority are different:

| Interaction             | Pending category | What the Host supplies                                      |
| ----------------------- | ---------------- | ----------------------------------------------------------- |
| `ask_user_question`     | `calls`          | A structured answer or an explicit tool failure             |
| Tool approval           | `approvals`      | An approve/deny decision, subject to fresh execution policy |
| Other client-side tools | `calls`          | A result defined by the selected client tool                |

An answer is not permission to execute a tool. See [Managed tools](managed-tools.md) for approval policy and [Client-side tools](client-tools.md) for application-defined external actions.

## Enable structured questions

Add `UserInteractionCapability` to your Agent definition. In an application that already supplies `agent_spec` and `model`:

```python
from a13n_harness import HarnessBuilder
from a13n_harness.capabilities import UserInteractionCapability

executable = HarnessBuilder().build(
    agent_spec,
    output_type=str,
    model=model,
    capabilities=(UserInteractionCapability(),),
)
```

The model can request `ask_user_question` when the current Host supports deferred tools. `RunBindings.deferred_tools_supported=False` omits the tool and its guidance. This applies to roots and children; [built-in inline children](delegation-and-codeact.md#host-managed-feedback) disable deferral, while a supporting Host can resume its own child.

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

Service exposes tool arguments and returned values as generic JSON. Define the types needed by your application rather than assuming its SDK exports built-in question types. For example, these TypeScript types describe the wire shape only; they are **application-owned**, not SDK imports:

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

A successful question call ends the Run with `status="suspended"`, `state`, and `deferred`. Retain the exact pending envelope and selected state. Once the person has answered, start a fresh Run with fresh bindings and a correlated `DeferredToolResume`.

The following function uses the `executable` configured above. It expects one question call and no approvals; the simulated response chooses the first option for each question. Replace that selection with your application's human interaction:

```python
from a13n_harness import DeferredToolResume, RunBindings
from a13n_harness.capabilities import AskUserQuestionRequest


async def answer_question(executable):
    first = await executable.run("Clarify the scope", bindings=RunBindings.embedded())
    if first.status != "suspended" or first.state is None or first.deferred is None:
        raise RuntimeError("Expected a pending question")
    requests = first.deferred
    if len(requests.calls) != 1 or requests.approvals:
        raise RuntimeError("This example expects one call and no approvals")
    call = requests.calls[0]
    if call.tool_name != "ask_user_question":
        raise RuntimeError("Expected the built-in question tool")
    question_request = AskUserQuestionRequest.model_validate(call.args_as_dict())
    answer = {
        "answers": {
            question.question: question.options[0].label
            for question in question_request.questions
        }
    }
    results = requests.build_results(calls={call.tool_call_id: answer})
    second = await executable.run(
        bindings=RunBindings.embedded(),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(requests, results),
    )
    return second.output_or_raise()
```

For a batch containing several calls or approvals, cover every pending item exactly once in its original category. The outer mapping uses `tool_call_id`; the question answer's inner mapping uses question text. Do not use `approve_all=True` as an answer to a question. [State and resume](state-and-resume.md#structured-suspension) covers complete feedback, fresh authorization, and durable recovery after an interrupted resume.

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

Replace `question-1` with the actual `tool_call_id`. Both maps are required and must cover the complete pending batch; use an empty map only for an empty category. An ordinary Thread message cannot resolve the wait, and optional resume `input` is additional context, not a replacement for answers or approval decisions. Service resumes into a distinct successor Run; the old Run stays waiting.

Send the request with the Service's authentication, workspace selection, and a stable `Idempotency-Key`. See [Service waiting Runs](../a13n-service/agents-and-runs.md#resume-a-waiting-run) and the [SDK guides](../a13n-service/sdks.md) for complete submission workflows. Harness UI owns its own interactive forms and Host lifecycle; these wire examples do not prescribe its UI.
