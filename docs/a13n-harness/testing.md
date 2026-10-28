---
title: Testing Harness applications
sidebarTitle: Testing
description: Build an offline test suite for output, saved history, streams, and application integrations.
---

Start with three offline tests: return an answer, restore a saved Thread, and consume a stream. Then add tests for the tools and resources your application actually uses. A deterministic Model keeps these tests fast and makes failures reproducible.

## Start with `FunctionModel`

Use the checkout and Python environment from [Getting started](getting-started.md). From the checkout root, install the locked development dependencies, including pytest and AnyIO:

```bash
uv sync --locked --package a13n-harness --group dev
```

Create `test_agent.py` with this complete module. The explicit `anyio_backend` fixture selects asyncio, so the test works outside this repository's test configuration.

```python title="test_agent.py"
from collections.abc import AsyncIterator

import pytest
from a13n_harness import AgentSpec, HarnessBuilder
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
```

Keep `test_agent.py` in the checkout root and run:

```bash
uv run python -m pytest -q test_agent.py
```

Expect `1 passed`. The Model counts earlier responses in the history instead of calling a provider. Harness construction, execution, output validation, and state export still run normally.

Harness makes streamed Model requests. Supply `FunctionModel(stream_function=...)`; a non-streaming `function` alone cannot serve this path. Yield strings for text, or `DeltaToolCalls` for deterministic tool calls. [Tools and dependencies](tools-and-dependencies.md) shows a complete tool-call example.

## Test continuation explicitly

Append this test to the same file. It saves the complete state in a temporary file, rebuilds the executable, and checks that the next Model request sees both turns.

```python
from pathlib import Path

from a13n_harness import HarnessState


async def test_saved_state_resumes_a_rebuilt_agent(tmp_path: Path) -> None:
    first = await build_agent().run("First turn")
    first.raise_for_status()
    assert first.state is not None
    checkpoint = tmp_path / "thread.json"
    checkpoint.write_text(first.state.model_dump_json(), encoding="utf-8")

    restored = HarnessState.model_validate_json(
        checkpoint.read_text(encoding="utf-8")
    )
    second = await build_agent().run("Second turn", previous_state=restored)

    assert second.output_or_raise() == "turn-2"
    assert second.thread_id == first.thread_id
    assert second.run_id != first.run_id
```

Run the same pytest command; expect `2 passed`. In your application suite, use the application's real save/load functions as well. Test its commit policy: failed or abandoned turns must not replace a checkpoint that the application intends to retain.

## Test streams as scoped resources

Append the third test to `test_agent.py`:

```python
from a13n_harness import HarnessRunResultEvent


async def test_stream_delivers_a_terminal_result() -> None:
    terminal = None
    async with build_agent().stream("First turn") as stream:
        async for item in stream:
            if isinstance(item, HarnessRunResultEvent):
                terminal = item.result

    assert terminal is not None
    assert terminal.output_or_raise() == "turn-1"
    assert terminal.state is not None
```

The same command now runs all three tests. Keep the `async with` around the entire consumer: it closes Run resources on completion, exceptions, and early exit. If your application forwards a stream to a caller, also test that the caller's early exit closes the application wrapper and its temporary resources.

## Protect provider prompt prefixes

For a Capability that transforms Model messages, test a complete multi-request Run:

1. Make the Model request a tool, then return a final answer.
2. Record the messages received on both requests using the public message codec.
3. Compare the first complete sequence with the equal-length prefix of the second.
4. Check that the final transformation also appears in the result history.

This catches changes made only to an outgoing request and lost from canonical history. If history replacement is the feature's intended behavior, assert that exact replacement. For Capability instructions, capture `AgentInfo.instructions` on each request and assert either stability or the intended transition.

## Separate definition and Run authority

Build stable behavior and tool definitions into the executable. Supply user-specific policy, model routing, and Web, document, media, or interaction collaborators through fresh Run bindings, as production code does.

For each tool integration, check both the Model's visible tool surface and the typed request delivered to the collaborator. Include a denied operation when the application owns an authorization rule. Use [Tools and dependencies](tools-and-dependencies.md#supply-a-live-application-dependency) to choose where each fake belongs.

## Test Environment integration at three levels

Choose the least expensive level that proves the behavior:

| Level                 | Setup                                         | What to check                                   |
| --------------------- | --------------------------------------------- | ----------------------------------------------- |
| Unit                  | Fake typed Environment ports or collaborators | Arguments, returned values, policy, errors      |
| Local integration     | Direct Local with a temporary directory       | Real file/process operations and cleanup        |
| Isolation integration | Local Envd with a built daemon                | EIP negotiation, isolation, lifecycle, recovery |

Direct Local exercises Host file/process access. Use Local Envd when the assertion concerns native isolation or the Environment Interaction Protocol (EIP). From a repository checkout prepared for Envd development, run:

```bash
make local-envd-test
```

See [Environment examples](../environments/examples.md) for provider setup. Keep tests that launch a daemon separate from the three offline tests above.

## Test plugins as installed packages

Test entry-point discovery in an isolated virtual environment with the extension installed. Exercise explicit selection, invalid configuration, fresh per-Run instances, cleanup, and failure handling. Patching the in-memory catalog skips the packaging behavior this test is meant to verify.

The [plugin example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins) includes offline tests for both installed entry points and direct-code composition.

## Keep one real-provider smoke test

Add a small opt-in test for credentials, the configured Model ID, and network access. Assert your application's output contract rather than exact generated wording. Keep it separate from the offline suite so normal development does not require a provider account.

## Repository examples

Use these as patterns when the application grows beyond the three starter tests:

- [Agent Application tests](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app/tests): repeated turns, restart, failure, early stream exit, and temporary Environment cleanup.
- [Harness tests](https://github.com/converge-ai-labs/agent-foundation/tree/main/packages/a13n-harness/tests): public events, state, Capabilities, Environments, observation, delegation, and plugins.
