---
title: Inputs and outputs
description: Enforce type-safe Pydantic or JSON Schema outputs, pass multimodal binary inputs, generate prompts after Environment entry, and handle all Run statuses.
---

Declare your output contract when building the Agent, pass text or multimodal inputs to each Run, and branch on the Run's terminal status before publishing results.

## Return typed or JSON Schema outputs

Save the following offline example as `output_example.py` and run `uv run python output_example.py`:

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
    print(output.model_dump_json())  # {"summary":"No findings","files_checked":3}


if __name__ == "__main__":
    asyncio.run(main())
```

Choose one output contract at build time (either `output_type` or `AgentSpec.output_schema`, never both):

- **Python type or `OutputSpec` (`output_type=ReviewSummary` or `output_type=str`):** Returns a validated Python instance.
- **Configuration-driven JSON Schema (`AgentSpec(output_schema=...)` with `output_type=None`):** Validates and returns a `dict[str, JsonValue]`.

## Handle all terminal statuses

Scripts can call `result.output_or_raise()` (or `result.raise_for_status()`), while interactive hosts should branch explicitly on `result.status`:

```python
result = await executable.run("Complete the task")

if result.status == "completed":
    output = result.output_or_raise()
elif result.status == "suspended":
    pending = result.deferred  # Save result.state and resume via DeferredToolResume
elif result.status == "failed":
    failure = result.failure
else:
    pass  # "cancelled": discard partial text as a business result
```

See [State and resume](state-and-resume.md) for checkpoint persistence (`result.all_messages()` / `result.new_messages()`), [Agents and Runs](agents-and-runs.md#stream-events) for `async with executable.stream(...)`, and [Stream Protocol](../a13n-stream-protocol/index.md) for AG-UI event projection.

## Pass multimodal content or generate input after Environment entry

Pass strings directly, or combine text with `BinaryContent` when using an image/media-capable Model (for Agent-selected workspace files, see [Multimedia understanding](multimedia-understanding.md)):

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

When the prompt depends on the entered Environment or assigned `run_id`, pass `input_factory` instead of `input`:

```python
from a13n_harness import RunPreparationContext


async def make_input(preparation: RunPreparationContext) -> str:
    return f"Return the review summary for Run {preparation.run_id}."


result = await executable.run(input_factory=make_input)
```
