---
title: Harness quickstart
sidebarTitle: Quickstart
description: Build your first type-safe Python agent offline, stream token events, maintain multi-turn thread state, and connect external model providers.
---

This quickstart guides you through building and running your first agent with **Harness**. You will begin with a completely offline, deterministic Python program that requires zero API keys or external services, then extend it to multi-turn thread continuation, event streaming, and cloud model providers.

## Requirements

Ensure you have Python 3.13 or newer, Git, and the [`uv`](https://docs.astral.sh/uv/) package manager installed.

Clone the repository and synchronize dependencies using the project's locked environment:

```bash
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
uv sync --locked --package a13n-harness
```

`uv sync` installs Harness along with Pydantic AI and testing utilities. Running the offline examples requires no internet connection.

## Run an offline Agent

Create a file named `app.py` in your repository root with the following minimal script:

```python title="app.py"
import asyncio
from collections.abc import AsyncIterator

from a13n_harness import AgentSpec, HarnessBuilder
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel


# 1. Define a deterministic offline responder function
async def respond(
    messages: list[ModelMessage],
    info: AgentInfo,
) -> AsyncIterator[str]:
    del messages, info
    yield "Hello from the Harness!"


# 2. Build the agent executable and execute a prompt
async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(instructions="You are a helpful assistant."),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )
    result = await executable.run("Say hello")
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

Execute the script with `uv`:

```bash
uv run python app.py
```

Output:

```text
Hello from the Harness!
```

Even though the mock responder returns static text, Harness executes the complete native run lifecycle—validating input parameters, managing run state, and packaging structured execution results.

## Understand the execution path

The core architecture separates the agent's static definition from its runtime execution:

| Component                     | Architecture role                                                                    |
| :---------------------------- | :----------------------------------------------------------------------------------- |
| `AgentSpec`                   | Declarative instructions, model identifiers, and static behavior policies            |
| `HarnessBuilder().build(...)` | Assembles instructions, models, and capabilities into a thread-safe executable       |
| `output_type=str`             | Type contract for the terminal result (accepts Python primitives or Pydantic models) |
| `executable.run(...)`         | Spawns a new logical run and awaits the terminal result asynchronously               |
| `result.output_or_raise()`    | Safely extracts the validated output or raises an exception on failure               |
| `result.state`                | Serialized checkpoint encapsulating message history and portable capability state    |

## Build once and continue a Thread

Agents often need to engage in multi-turn conversations. To continue a conversation within the same thread, pass the `previous_state` returned from the prior turn into your next call.

Update `main()` in `app.py`:

```python
async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )

    # First turn
    first = await executable.run("First greeting")
    print(first.output_or_raise())

    # Second turn: pass previous_state to continue the conversation thread
    second = await executable.run("Second greeting", previous_state=first.state)
    print(second.output_or_raise())

    assert second.thread_id == first.thread_id
    assert second.run_id != first.run_id
    print("Conversation continuity verified: same Thread, new Run.")
```

Run `uv run python app.py` again. The assertions confirm that both executions share the same `thread_id` while generating distinct `run_id` audit traces.

To persist conversations across application restarts, serialize `result.state` to JSON and store it in your database. See [State and Resume](state-and-resume.md).

## Stream public events

For interactive chat UIs and command-line streaming, consume live event streams using `executable.stream()`:

```python
from a13n_harness import HarnessEvent, HarnessRunResultEvent
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )

    async with executable.stream("Stream a greeting") as stream:
        async for item in stream:
            if isinstance(item, HarnessRunResultEvent):
                item.result.raise_for_status()
            elif isinstance(item, HarnessEvent):
                event = item.event
                if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                    print(event.part.content, end="", flush=True)
                elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                    print(event.delta.content_delta, end="", flush=True)
    print()
```

The `async with` context manager ensures that all network connections, tool resources, and background tasks are properly cleaned up upon stream completion.

## Use a real model

Once you have validated the execution loop offline, switch to an external LLM provider such as OpenAI, Anthropic, or Gemini:

1. Add `import os` to `app.py`.
2. Configure the model in `AgentSpec`:

```python
    executable = HarnessBuilder().build(
        AgentSpec(
            model=os.environ["HARNESS_MODEL"],
            instructions="You are an expert software engineer. Provide concise, actionable answers.",
        ),
        output_type=str,
    )
```

3. Export your provider credentials and model identifier in your shell environment:

```bash
export OPENAI_API_KEY="sk-..."
export HARNESS_MODEL="openai-responses:gpt-4o"
uv run python app.py
```

Harness resolves the provider string, initializes authenticated HTTP transports, and executes the conversation against the upstream API. For client customization and auth proxies, see [Model authentication](model-authentication.md).

## Add Capabilities

Modular features—such as runtime context, note-taking, or custom toolsets—are attached via **Capabilities**:

```python
from a13n_harness.capabilities import RuntimeContextCapability, WorkingStateCapability


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
        capabilities=(
            RuntimeContextCapability(),
            WorkingStateCapability(),
        ),
    )
    result = await executable.run("Say hello")
    print(result.output_or_raise())
```

Explore [Capabilities](capabilities.md) to discover built-in extensions for memory, environment sandboxes, and tool discovery.

## Troubleshoot the first run

Common setup issues and their resolutions:

| Symptom                                               | Primary check                                                                               |
| :---------------------------------------------------- | :------------------------------------------------------------------------------------------ |
| `ModuleNotFoundError: No module named 'a13n_harness'` | Ensure you run scripts via `uv run python app.py` within the synchronized workspace         |
| Python version incompatibility                        | Harness requires Python 3.13 or newer. Verify with `python3 --version`                      |
| Conflicting model configurations                      | Provide the model either via builder `model=` or `AgentSpec.model`, not both simultaneously |
| Provider authentication error                         | Verify your API key environment variable and model ID access permissions                    |
| Undefined import names                                | Ensure you have synchronized the latest package definitions with `uv sync`                  |

## Next steps

Now that your core runtime loop is operational:

1. [Tools and dependencies](tools-and-dependencies.md): Register custom Python functions and handle dependency injection.
2. [State and Resume](state-and-resume.md): Store and restore serialized checkpoints in production databases.
3. [Testing](testing.md): Write fast, deterministic unit test suites for complex agent behaviors.
4. [Environments](../environments/index.md): Grant agents sandboxed file and command execution capabilities.
