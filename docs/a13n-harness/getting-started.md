---
title: Harness quickstart
sidebarTitle: Quickstart
description: Run your first Agent offline, continue its Thread, stream its answer, and connect a provider.
---

Build a small Python program and get a result before configuring a provider. Then reuse the same program to continue a Thread and stream output. The offline steps need no account, API key, or external service.

## Requirements

Install Python 3.13 or later, Git, and [uv](https://docs.astral.sh/uv/). These guides follow the source API on `main`; use the repository and its lockfile together:

```bash
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
uv sync --locked --package a13n-harness
```

Keep your terminal in this checkout for the following commands. `uv` installs Harness and its dependencies, including the deterministic `FunctionModel` used below. Dependency installation needs network access; running the offline example does not.

## Run an offline Agent

1. Create `app.py` in the checkout root with this complete program:

   ```python title="app.py"
   import asyncio
   from collections.abc import AsyncIterator

   from a13n_harness import AgentSpec, HarnessBuilder
   from pydantic_ai.messages import ModelMessage
   from pydantic_ai.models.function import AgentInfo, FunctionModel


   async def respond(
       messages: list[ModelMessage],
       info: AgentInfo,
   ) -> AsyncIterator[str]:
       del messages, info
       yield "Hello from the Harness"


   async def main() -> None:
       executable = HarnessBuilder().build(
           AgentSpec(),
           output_type=str,
           model=FunctionModel(stream_function=respond),
       )
       result = await executable.run("Say hello")
       print(result.output_or_raise())


   if __name__ == "__main__":
       asyncio.run(main())
   ```

2. Run it from the same directory:

   ```bash
   uv run python app.py
   ```

3. Check the output:

   ```text
   Hello from the Harness
   ```

The function supplies the model response, but Harness performs the real Run lifecycle. This confirms that your installation, Agent construction, and result handling work together.

## Understand the execution path

| Code                          | Responsibility                                                                |
| ----------------------------- | ----------------------------------------------------------------------------- |
| `AgentSpec()`                 | Agent instructions and declarative settings                                   |
| `HarnessBuilder().build(...)` | Combine the definition, model, and output contract into a reusable executable |
| `output_type=str`             | Require a final text result                                                   |
| `executable.run(...)`         | Start a Run and wait for its terminal result                                  |
| `result.output_or_raise()`    | Return completed output or raise for a non-completed result                   |
| `result.state`                | Carry messages and portable state into a later Run                            |

Here the concrete model is a builder argument, so `AgentSpec.model` stays unset. Choose one model source. Keep `output_type` explicit even for a text-only Agent.

## Build once and continue a Thread

Keep the imports, `respond()`, and final `asyncio.run(main())` from the first program. Replace only `main()` with:

```python
async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )
    first = await executable.run("Say hello")
    print(first.output_or_raise())
    second = await executable.run("Say hello again", previous_state=first.state)
    print(second.output_or_raise())
    assert second.thread_id == first.thread_id
    assert second.run_id != first.run_id
    print("Same Thread, new Run")
```

Run `uv run python app.py` again. It prints two greetings and `Same Thread, new Run`. The assertions check continuation identity; the fixed offline responder still returns the same text for every prompt.

Save the returned `HarnessState` when you need to continue after restarting the program. The [state guide](state-and-resume.md) shows the JSON round trip and storage workflow. Your application reconstructs current credentials and live resources separately.

## Stream public events

To display text as it arrives, start from the original offline program. Add these imports, replace `main()` with the version below, and keep the final `asyncio.run(main())`:

```python
from a13n_harness import HarnessEvent, HarnessRunResultEvent
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )
    async with executable.stream("Say hello") as stream:
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

Run the file again and check for the greeting. Text events drive the display; the terminal `HarnessRunResultEvent` tells you whether execution completed. The `async with` scope closes Run resources, including when you stop consuming early. Use that terminal result's `state` when adding persistence to a streaming application.

## Use a real model

Once the offline program works, connect an account you can use. This example uses the OpenAI Responses integration; choose a model ID available to your account.

1. Return to the original `app.py` and add `import os`.

2. Replace its builder call inside `main()` with:

   ```python
       executable = HarnessBuilder().build(
           AgentSpec(
               model=os.environ["HARNESS_MODEL"],
               instructions="Answer clearly and concisely.",
           ),
           output_type=str,
       )
   ```

3. Set the credential and selection in your process environment. In a POSIX shell, replace both placeholders:

   ```bash
   export OPENAI_API_KEY='YOUR_API_KEY'
   export HARNESS_MODEL='openai-responses:YOUR_MODEL_ID'
   uv run python app.py
   ```

The answer now comes from the provider and can vary. The offline `respond()` function and its model imports can be removed. Keep secrets out of source control. Other providers, application-owned clients, and short-lived credentials are covered in [Models](models.md) and [Authentication and HTTP clients](model-authentication.md).

## Add Capabilities

Add features when the Agent needs them. For example, the following replacement builder call adds runtime context and working notes/tasks to the original offline program. Put the imports at the top and the builder call inside `main()`:

```python
from a13n_harness.capabilities import RuntimeContextCapability, WorkingStateCapability


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
        capabilities=(RuntimeContextCapability(), WorkingStateCapability()),
    )
    result = await executable.run("Say hello")
    print(result.output_or_raise())
```

The greeting remains unchanged because the deterministic responder does not choose the added tools. Continue with [Tools and dependencies](tools-and-dependencies.md) for an offline example that actually calls a function, or [Capabilities](capabilities.md) to select built-in features.

## Troubleshoot the first run

| Symptom                                    | Check                                                                                             |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| Unsupported Python version                 | Use Python 3.13 or later and repeat the locked sync                                               |
| `No module named a13n_harness`             | Run through `uv run` in the synchronized checkout                                                 |
| Model-selection conflict                   | Use either a builder `model=` or `AgentSpec.model`, not both                                      |
| Missing output contract                    | Keep `output_type=str`, or choose the explicit schema workflow in the input/output guide          |
| Offline greeting works, provider run fails | Check the selected model ID, account access, credential, and provider error                       |
| A pasted fragment has undefined names      | Apply the stated replacement to the original program; fragments reuse its imports and `respond()` |

## Next steps

1. [Call a Python tool](tools-and-dependencies.md) and inspect its result.
2. [Save and restore a Thread](state-and-resume.md) across program invocations.
3. [Test offline](testing.md) before connecting external services.

For application structure, read [Agents and Runs](agents-and-runs.md). For file and command access, continue with [Environments](environments.md). The [Agent Application example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app) combines repeated streaming turns, persistence, and restart recovery.
