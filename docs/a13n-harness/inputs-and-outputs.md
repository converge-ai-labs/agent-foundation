---
title: Inputs and outputs
description: Start or resume a Run with text and media, and handle validated outputs and every terminal outcome.
---

An input starts or resumes one logical Run. An output is the validated application value from a completed Run. Tool returns, progress events, summaries, and saved state are not interchangeable with that output.

Harness uses Pydantic AI's native input and output types. It adds a normalized result so your application can distinguish completion from suspension, failure, and cancellation.

## Return a structured value

This complete offline example validates a Pydantic model. Save it as `output_example.py` in the [quickstart workspace](getting-started.md) and run `uv run python output_example.py`.

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

The model in this example supplies test data; it does not inspect files. Pydantic AI validates the output, and Harness wraps the result with status, usage, correlation, and continuation state.

## Choose the output contract at build time

Use a Python type or native Pydantic AI `OutputSpec` through `output_type`. Alternatively, set `AgentSpec.output_schema` for a JSON-schema-defined dictionary output. Do not supply both.

The contract belongs to the executable and cannot change per Run. Build another executable if the application requires a different output contract. Native output functions and output modes keep Pydantic AI's validation and retry behavior; Harness does not invent a second output parser.

## Handle all terminal outcomes

```python
result = await executable.run("Complete the task")

if result.status == "completed":
    output = result.output_or_raise()
    # The application decides whether and how to publish this value.
elif result.status == "suspended":
    pending = result.deferred
    # Persist an accepted checkpoint and ask the appropriate human/client.
elif result.status == "failed":
    failure = result.failure
    # Report the safe failure and inspect any available checkpoint.
else:
    # Cancelled: do not treat partial text as a successful business result.
    pass
```

Use `raise_for_status()` when only successful completion is acceptable, or `output_or_raise()` when you also need the typed output. A suspended Run requires [deferred resume](state-and-resume.md), not blind resubmission of the original prompt.

A terminal result is a process-local candidate after cleanup, **not** proof that your database committed it or that the client received it. Persist and deliver under your application's policy.

## Text and multimodal input

For ordinary text, pass a string directly:

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

The embedding application owns file access, size bounds, and model support. Use [Environment multimedia understanding](multimedia-understanding.md) when the Agent should read authorized Environment files, rather than letting application paths become implicit model authority.

## Produce input after Environment entry

Use `input_factory` when the input depends on the current entered Environment or newly assigned Run identity:

```python
from a13n_harness import RunPreparationContext


async def make_input(preparation: RunPreparationContext) -> str:
    return f"Inspect the workspace for Run {preparation.run_id}."


result = await executable.run(input_factory=make_input, environment=environment)
```

`input` and `input_factory` are mutually exclusive. The factory runs once before model execution. The Host must supply a fresh Environment; imported state does not recreate its clients or credentials.

## Continue history or stream progress

Pass `previous_state=first.state` to continue a Thread; persist the complete `HarnessState` for restart, not just displayed text. `all_messages()` returns detached represented history and `new_messages()` returns this Run's additions.

Use `async with executable.stream(...)` for incremental public events. Text deltas are progress, not a replacement for the terminal result. A parent stream may also contain child events with different Run correlation; do not mistake a child's terminal event for root completion.

See [Agents and Runs](agents-and-runs.md#stream-events) for stream ownership and [Stream Protocol](../a13n-stream-protocol/index.md) for AG-UI projection.
