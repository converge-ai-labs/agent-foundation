---
title: Testing Harness applications
sidebarTitle: Testing
description: Test agents at their own boundaries with deterministic models, streams, state, and Environments.
---

Test a Harness application at the same boundaries it owns: deterministic model behavior, terminal results, continuation state, public stream events, Environment lifecycle, and selected extensions. Keep provider-network tests separate from the fast application suite.

## Start with `FunctionModel`

Pydantic AI's `FunctionModel` runs the real Agent and Harness paths without an API key:

```python
from collections.abc import AsyncIterator

import pytest
from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
)
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

pytestmark = pytest.mark.anyio


async def respond(
    messages: list[ModelMessage],
    info: AgentInfo,
) -> AsyncIterator[str]:
    del messages, info
    yield "expected output"


async def test_agent_returns_expected_output() -> None:
    model = FunctionModel(stream_function=respond)
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model,
    )

    result = await executable.run("Complete the task")

    assert result.output_or_raise() == "expected output"
    assert result.state is not None
```

This tests Harness construction, the Pydantic AI loop, terminal normalization, and state export. Use a streaming function when the test needs text deltas, tool calls, deferred interaction, or provider-history shapes.

## Test continuation explicitly

A continued Thread preserves `thread_id` and receives a fresh `run_id`:

```python
first = await executable.run("First turn")
second = await executable.run(
    "Second turn",
    previous_state=first.state,
)

assert second.thread_id == first.thread_id
assert second.run_id != first.run_id
```

For application persistence, round-trip the complete `HarnessState` through the same serializer and store used by the application. Also test that failed or abandoned work does not replace the last committed checkpoint.

## Protect provider prompt prefixes

When a Capability changes model-visible messages, drive at least two model requests inside one Run. Capture the messages received by the Model, compare each complete sequence with the equal-length prefix of the next request through the public message codec, and confirm the final transformation remains in canonical result history. This detects request-local transformations that were not written back to active history. If the Capability intentionally replaces history, assert that exact transition instead of weakening or omitting the prefix check.

When that Capability also contributes instructions, capture `AgentInfo.instructions` for every request and require them to remain identical. If the Capability contract intentionally changes instructions, assert the exact expected transition instead of weakening the stability check.

## Test streams as scoped resources

Consume streams inside their async context and require a terminal result:

```python
from a13n_harness import HarnessRunResultEvent

items = []
terminal = None
async with executable.stream("Stream the answer") as stream:
    async for item in stream:
        items.append(item)
        if isinstance(item, HarnessRunResultEvent):
            terminal = item.result

assert items
assert terminal is not None
terminal.raise_for_status()
```

Add a test that exits the consumer early when the application exposes streaming to callers. The application must still close the stream scope so temporary Environment resources and run-local collaborators are released.

## Separate definition and run authority

Test definition-selected Capabilities with fixed fakes. Inject user-specific policy, model routing, media, document, Web, or interaction collaborators through fresh run bindings. This keeps tests aligned with the production ownership boundary and prevents credentials or mutable authority from leaking into reusable definitions.

When a Capability exposes tools, test both sides:

1. the model sees only the expected tool surface;
2. the collaborator receives the expected typed request and policy context.

## Test Environment integration at three levels

| Level                 | Use                                              | Assert                                                            |
| --------------------- | ------------------------------------------------ | ----------------------------------------------------------------- |
| Unit                  | Fake typed Environment ports or collaborators    | Tool arguments, result shaping, policy, and errors                |
| Local integration     | Direct Local provider with a temporary directory | Real file/process behavior and cleanup                            |
| Isolation integration | Local Envd with a built `a13n-envd` binary       | EIP negotiation, isolation prerequisites, lifecycle, and recovery |

Direct Local is not an isolation substitute. A test that must prove the EIP or native-isolation boundary should use the real Local Envd path.

From this repository, the focused Local Envd integration gate is:

```bash
make local-envd-test
```

## Test plugins as installed packages

For entry-point discovery, build or install the extension distribution in an isolated environment rather than patching the catalog. Verify explicit selection, configuration validation, fresh per-run instances, cleanup, and failure isolation.

The repository's [plugin example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins) includes entry-point and direct-code paths with offline tests.

## Keep one real-provider smoke test

A small opt-in smoke test can verify provider credentials, model naming, and network integration. Do not make the main suite depend on a live model. Keep model responses out of exact assertions unless the provider and seed make them deterministic; assert the application contract instead.

## Repository examples

- [Agent Application tests](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app/tests) cover repeated turns, restart recovery, failure, early stream exit, and temporary Environment cleanup.
- [Harness tests](https://github.com/converge-ai-labs/agent-foundation/tree/main/packages/a13n-harness/tests) show focused public-boundary tests for events, state, Capabilities, Environments, observation, delegation, and plugins.
