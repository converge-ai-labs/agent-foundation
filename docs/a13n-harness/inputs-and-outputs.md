---
title: Inputs and outputs
description: Start or resume a Run with text and media, and handle validated outputs and every terminal outcome.
---

Give your application a typed result it can use directly. Define the output when you build the Agent, pass text or media to a Run, then handle the Run's status before publishing its output.

## Return a structured value

This complete offline example validates a Pydantic model. Save it as `output_example.py` in the [quickstart checkout](getting-started.md) and run `uv run python output_example.py`.

```python
import asyncio

from a13n_harness import AgentSpec, HarnessBuilder
from pydantic import BaseModel, Field
from pydantic_ai.models.test import TestModel


class ReviewSummary(BaseModel):
    summary: str
    files_checked: int = Field(ge=0)


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(instructions="Return a bounded review summary."),
        output_type=ReviewSummary,
        model=TestModel(
            custom_output_args={"summary": "No findings", "files_checked": 3}
        ),
    )
    result = await executable.run("Review the supplied changes.")
    output = result.output_or_raise()
    assert isinstance(output, ReviewSummary)
    assert output.files_checked == 3
    print(output.model_dump_json())


if __name__ == "__main__":
    asyncio.run(main())
```

Expected output:

```json
{"summary":"No findings","files_checked":3}
```

`TestModel` supplies fixed data so you can test your application without credentials. `output_type=ReviewSummary` defines the validated value, and `output_or_raise()` returns that value only after successful completion. To perform a real review, replace the test Model with your [configured Model](models.md) and supply the changes as input or through authorized tools.

## Choose the output contract at build time

Use `output_type=str` for text, a Python type such as `ReviewSummary` for structured values, or a native Pydantic AI `OutputSpec` for its output modes and functions. The contract belongs to the executable; build another executable when you need a different contract.

If your application loads schemas from configuration, use `AgentSpec.output_schema`. To try that path in the example, replace the build call with this block and set `output_type=None`:

```python
executable = HarnessBuilder().build(
    AgentSpec(
        instructions="Return a bounded review summary.",
        output_schema=ReviewSummary.model_json_schema(),
    ),
    output_type=None,
    model=TestModel(
        custom_output_args={"summary": "No findings", "files_checked": 3}
    ),
)
```

Then replace the output assertions and print with:

```python
assert isinstance(output, dict)
assert output["files_checked"] == 3
print(output)
```

The schema path returns a validated dictionary rather than a `ReviewSummary` instance. Select exactly one output source: `output_type` or `output_schema`. Validation and output retries run through Pydantic AI; Harness also enforces the declared JSON Schema on completed dictionary values.

## Handle all terminal outcomes

`output_or_raise()` is convenient for a script that requires success. An interactive application needs a branch for each status. Inside `main()`, replace the run-and-print portion with this pattern:

```python
result = await executable.run("Complete the task")

if result.status == "completed":
    output = result.output_or_raise()
    # The application decides whether and how to publish this value.
elif result.status == "suspended":
    pending = result.deferred
    # Save result.state and collect answers for these deferred requests.
elif result.status == "failed":
    failure = result.failure
    # Report the safe failure and inspect any available checkpoint.
else:
    # Cancelled: do not treat partial text as a successful business result.
    pass
```

Use `raise_for_status()` when only successful completion is acceptable, or `output_or_raise()` when you also need the typed output. A suspended Run requires [deferred resume](state-and-resume.md), not blind resubmission of the original prompt.

Once cleanup finishes, the result includes status, usage, Thread and Run IDs, and any available continuation state. Your application then saves the result and delivers it to the client. Those application steps determine whether it is durably stored and received.

## Text and multimodal input

The following snippets run inside `main()` with the same `executable`. For ordinary text, pass a string directly:

```python
result = await executable.run("Explain the change")
```

Native Pydantic AI user-content sequences can carry text and supported media together. For example, this fragment requires an image-capable effective Model and a readable image file:

```python
from pathlib import Path
from pydantic_ai import BinaryContent

result = await executable.run(
    [
        "Describe this screenshot.",
        BinaryContent(data=Path("screenshot.png").read_bytes(), media_type="image/png"),
    ]
)
```

Here, the application reads the file and supplies its bytes. Check file access, size bounds, and Model support in that application. To let the Agent choose and read files from an authorized Environment, configure [Environment multimedia understanding](multimedia-understanding.md).

## Produce input after Environment entry

Use `input_factory` when input depends on the entered Environment or newly assigned Run identity. This version uses only the identity and works with the offline example. Add the import and function at module level, then replace the `executable.run(...)` call inside `main()`:

```python
from a13n_harness import RunPreparationContext


async def make_input(preparation: RunPreparationContext) -> str:
    return f"Return the review summary for Run {preparation.run_id}."


result = await executable.run(input_factory=make_input)
```

The factory runs once after Environment entry and before model execution. To inspect a workspace there, pass a fresh `environment=` entry as shown in [Environment tools](environments.md) and use `preparation.environment`. Supply either `input` or `input_factory`, not both. Restored state carries conversation data; the Host supplies current clients and credentials.

## Continue history or stream progress

To continue the example in the same Thread, add this after the first completed result:

```python
follow_up = await executable.run(
    "Return the review summary again.", previous_state=result.state
)
assert follow_up.thread_id == result.thread_id
assert follow_up.run_id != result.run_id
assert follow_up.output_or_raise() == result.output_or_raise()
```

The fixed test data makes both outputs equal. A real Model can answer from the previous conversation. For restart, persist the complete `HarnessState` as shown in [State and Resume](state-and-resume.md). Use `all_messages()` for a detached view of the complete result history and `new_messages()` for the messages this Run added.

Use `async with executable.stream(...)` for incremental public events, and read `stream.result` after leaving the scope. Display text deltas as progress and use the terminal result for the application output. Child events can appear in the parent stream; correlate events to their Run rather than treating a child's terminal event as root completion.

See [Agents and Runs](agents-and-runs.md#stream-events) for stream ownership and [Stream Protocol](../a13n-stream-protocol/index.md) for AG-UI projection.
