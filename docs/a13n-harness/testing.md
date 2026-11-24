---
title: Testing Harness applications
sidebarTitle: Testing
description: Write fast, deterministic unit and integration tests for Harness agents using FunctionModel, TestModel, and three-tier Environment verification.
---

Start with three fast offline tests—returning an answer, restoring a saved Thread, and consuming a stream—then add targeted tests for tools, Environments, and plugins. Deterministic models keep suites reproducible without network credentials.

## Write the core offline suite with `FunctionModel`

Install the development dependencies (`uv sync --locked --package a13n-harness --group dev`), save `test_agent.py`, and run `uv run python -m pytest -q test_agent.py`:

```python title="test_agent.py"
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, HarnessRunResultEvent, HarnessState
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def respond(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str]:
    del info
    turns = 1 + sum(isinstance(message, ModelResponse) for message in messages)
    yield f"turn-{turns}"


def build_agent():
    return HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )


async def test_agent_returns_expected_output() -> None:
    result = await build_agent().run("First turn")
    assert result.output_or_raise() == "turn-1"
    assert result.state is not None


async def test_saved_state_resumes_a_rebuilt_agent(tmp_path: Path) -> None:
    first = await build_agent().run("First turn")
    assert first.state is not None
    checkpoint = tmp_path / "thread.json"
    checkpoint.write_text(first.state.model_dump_json(), encoding="utf-8")

    restored = HarnessState.model_validate_json(checkpoint.read_text(encoding="utf-8"))
    second = await build_agent().run("Second turn", previous_state=restored)

    assert second.output_or_raise() == "turn-2"
    assert second.thread_id == first.thread_id and second.run_id != first.run_id


async def test_stream_delivers_a_terminal_result() -> None:
    terminal = None
    async with build_agent().stream("First turn") as stream:
        async for item in stream:
            if isinstance(item, HarnessRunResultEvent):
                terminal = item.result

    assert terminal is not None and terminal.output_or_raise() == "turn-1"
```

Because Harness issues streamed Model requests, always pass `FunctionModel(stream_function=...)` (yielding `str` chunks or `DeltaToolCalls` as shown in [Tools and dependencies](tools-and-dependencies.md)). Also test your Host's real checkpoint save/load logic so failed or cancelled turns never overwrite the last committed checkpoint, and verify early consumer exit inside `async with executable.stream(...)`.

## Verify capabilities, prompt prefixes, and bindings

- **Prompt prefix stability:** When a Capability transforms Model messages, run a multi-step test (tool call followed by final answer) and assert that the first request's message sequence matches the equal-length prefix of the second request and persists in canonical history—preserving provider prompt caching.
- **Definition vs. Run authority:** Build static instructions and tools into the executable, and supply per-request collaborators via fresh `RunBindings` ([Tools and dependencies](tools-and-dependencies.md#supply-a-live-application-dependency)). Assert both the model-visible schema and denied policy paths.
- **Installed plugins:** Test entry-point discovery in an isolated virtual environment with your package installed rather than patching the in-memory catalog (see [`examples/plugins`](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins)).

## Test Environments at three levels and keep one smoke test

Choose the lightest Environment tier that proves the contract:

| Level                 | Setup                                         | What to verify                                  |
| --------------------- | --------------------------------------------- | ----------------------------------------------- |
| Unit                  | Fake typed Environment ports or collaborators | Arguments, returned values, policy, errors      |
| Local integration     | Direct Local with `tmp_path`                  | Real file/process operations and cleanup        |
| Isolation integration | Local Envd daemon (`make local-envd-test`)    | EIP negotiation, isolation, lifecycle, recovery |

Keep daemon tests and one opt-in real-provider smoke test (asserting typed output contracts rather than exact prose) separate from the default offline suite. For full patterns, inspect [`examples/agent-app/tests`](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app/tests) and [`packages/a13n-harness/tests`](https://github.com/converge-ai-labs/agent-foundation/tree/main/packages/a13n-harness/tests).
