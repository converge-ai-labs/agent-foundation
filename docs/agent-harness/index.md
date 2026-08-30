# Agent Harness

`a13n-harness` is an embeddable, process-local runtime for building Pydantic AI agents with consistent composition, Environment access, continuation state, observation, and extension boundaries.

Use it as a Python library inside the application that already owns identity, policy, persistence, and delivery. It is not a hosted service and it does not replace the Pydantic AI Agent loop.

## Quick start

These guides track `main` and target the next Harness release. Until that release is published, use the locked source workspace:

```bash
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
uv sync --locked --package a13n-harness
```

```python
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

    async with executable:
        result = await executable.run("Say hello")

    print(result.output_or_raise())


asyncio.run(main())
```

Save the example as `app.py` and run it with `uv run python app.py` from the repository root.

This example is deterministic and needs no model credentials. The [Getting Started guide](getting-started.md) explains every boundary and shows how to select a real model.

## What the Harness adds

Pydantic AI remains responsible for models, messages, tools, Toolsets, Capabilities, output validation, deferred values, native events, and the Agent loop. The Harness adds reusable boundaries around that loop:

- code-first `AgentDefinition` and `HarnessBuilder` construction;
- one reusable `ExecutableAgent` with deterministic cleanup;
- fresh typed run context and collaborators for each logical run;
- one canonical event stream, terminal result, usage record, and correlation model;
- portable `HarnessState`, continuation, forking, checkpoints, and deferred resume;
- provider-neutral Environment operations and topology;
- trusted middleware, Skills, inline delegation, and restricted CodeAct;
- mandatory message-integrity and tool-result boundaries;
- OpenTelemetry traces and metrics selected at the embedding process boundary.

The smallest path is:

```text
AgentSpec -> HarnessBuilder -> ExecutableAgent -> run or stream -> HarnessRunResult
```

`RunBindings` is optional for the embedded default. Supply fresh bindings when current identity, model routing, provider collaborators, policy, or other run authority must be explicit.

## Capabilities all the way down

Stable Agent behavior is composed as Pydantic AI Capabilities. The Harness provides first-party Capability families while preserving native Pydantic AI composition.

| Need                                             | Capability or guide                                                                         |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| Runtime context and model-readable working state | `RuntimeContextCapability`, `WorkingStateCapability`                                        |
| Files, commands, processes, output, and ports    | `DynamicEnvironmentCapability` and [Environments](environments.md)                          |
| Human clarification or deferred approval         | `UserInteractionCapability`                                                                 |
| Media, documents, and Web integrations           | [Capabilities](capabilities.md) and [Multimedia Understanding](multimedia-understanding.md) |
| MCP servers                                      | Native `MCP` or Harness `ContextualMCP`                                                     |
| Child Agents and restricted Python orchestration | [Delegation and CodeAct](delegation-and-codeact.md)                                         |
| Packaged procedural knowledge                    | [Skills](skills.md)                                                                         |
| Known provider-history repair                    | `SelfHealingModelCapability`                                                                |
| Trusted outer middleware                         | [Plugins and Extensions](plugins.md)                                                        |

Definition Capabilities describe stable behavior. Credentials, authorization, provider clients, user-specific selection, and other current authority belong in fresh run bindings or Host-owned integrations.

## When to use the Harness

Use the Harness when an application needs one or more of these boundaries:

- a reusable definition and run lifecycle shared across several Agents;
- portable continuation state beyond raw message history;
- provider-neutral files, shell, process, output, or port tools;
- a canonical public stream and normalized terminal result;
- usage attribution across root, child, and mixed-model work;
- trusted plugins, packaged Skills, delegation, or CodeAct;
- a clear path from embedded execution to Host-owned durable lifecycle.

Use Pydantic AI directly when its native `Agent` surface already satisfies the application and none of these additional boundaries is needed.

## Choose a guide

| Goal                                                     | Guide                                                   |
| -------------------------------------------------------- | ------------------------------------------------------- |
| Run the smallest offline Agent, then select a real model | [Getting Started](getting-started.md)                   |
| Test definitions, streams, state, and integrations       | [Testing](testing.md)                                   |
| Build definitions; configure models; run and stream      | [Agents and Runs](agents-and-runs.md)                   |
| Select optional behavior and run collaborators           | [Capabilities](capabilities.md)                         |
| Expose files, commands, processes, output, or ports      | [Environments](environments.md)                         |
| Continue, fork, suspend, and resume                      | [State and Resume](state-and-resume.md)                 |
| Understand image, video, and audio files                 | [Multimedia Understanding](multimedia-understanding.md) |
| Use child Agents or restricted Python                    | [Delegation and CodeAct](delegation-and-codeact.md)     |
| Discover and select Skill packages                       | [Skills](skills.md)                                     |
| Export traces, metrics, events, and usage                | [Observation](observation.md)                           |
| Add trusted middleware or Environment extensions         | [Plugins and Extensions](plugins.md)                    |
| Add durable persistence, fencing, and delivery           | [Embedding in a Host](hosting.md)                       |

## Ownership at a glance

| Concern                                                               | Owner                |
| --------------------------------------------------------------------- | -------------------- |
| Agent loop, models, messages, native tools, output validation         | Pydantic AI          |
| Process-local definition, run, events, result, and continuation state | Agent Harness        |
| Provider resource lifecycle and runtime attachments                   | Environment Provider |
| Credentials, current authorization, durable records, and delivery     | Application or Host  |
| Product experience and business policy                                | Product              |

Passing an `EnvironmentProvider` to a run gives the Harness one temporary resource lifecycle. Passing an entered `EnvironmentResource` keeps the outer lifecycle with the Host and gives the Harness one fresh attachment per run. `HarnessState` can preserve continuation data, but it never restores credentials or current authority.

## Runnable examples

- [Agent Application](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app) demonstrates repeated offline streaming turns, successful-turn state persistence, restart recovery, and one temporary local Environment per turn.
- [Plugin Integration](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins) demonstrates packaged Harness middleware and Environment extensions.

Both examples use deterministic `FunctionModel` implementations and are tested without model credentials.

## Version policy

Agent Harness, Environment Provider, and Agent Stream Protocol form one release group and publish the same version. The project is currently refining its 0.x public contracts; review release notes before upgrading across versions.

These pages are user documentation. Normative architecture, invariants, and compatibility rules remain in the [Agent Harness specification](https://github.com/converge-ai-labs/agent-foundation/tree/main/spec/agent-harness).
