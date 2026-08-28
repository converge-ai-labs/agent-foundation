# Agent Foundation

[![CI](https://github.com/converge-ai-labs/agent-foundation/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/converge-ai-labs/agent-foundation/actions/workflows/ci.yml)
[![Documentation](https://img.shields.io/badge/docs-agent--foundation-blue)](https://agent-foundation-docs.converge.ai/)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

**Build agents as libraries, give them portable environments, and embed them in the products that own their lifecycle.**

Agent Foundation is a Python-first open-source foundation for agents and multi-agent systems. Its implemented surfaces extend [Pydantic AI](https://ai.pydantic.dev/) with a process-local Agent Harness, provider-neutral Environment boundaries, an isolated environment daemon, and a typed stream projection.

> Agent Foundation is under active 0.x development. Public contracts are usable but may change between releases while the project approaches its first stable release.

<!-- TODO(maintainers): Add package badges after the next documentation-aligned release. -->

<!-- TODO(maintainers): Add a short product demo when the end-user surfaces stabilize. -->

## Start with the Agent Harness

These examples track `main` and target the next Harness release. Until that release is published, use the locked source workspace:

```bash
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
uv sync --locked --package a13n-harness
```

<!-- TODO(maintainers): Replace the source setup with `pip install a13n-harness` after the documentation-aligned release. -->

Save the following deterministic Agent as `app.py`; it needs no API key:

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
    yield "Hello from Agent Foundation"


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

```bash
uv run python app.py
```

Continue with the [Getting Started guide](https://agent-foundation-docs.converge.ai/agent-harness/getting-started/) for real models, streaming, state, and Capabilities.

## What are you building?

| Goal                                                    | Start here                                                                                    |
| ------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| Embed an Agent in a Python application                  | [Agent Harness](https://agent-foundation-docs.converge.ai/agent-harness/)                     |
| Add files, commands, processes, or ports                | [Environment overview](https://agent-foundation-docs.converge.ai/environments/)               |
| Run those operations behind a native isolation boundary | [`agent-envd`](https://agent-foundation-docs.converge.ai/agent-envd/)                         |
| Project Harness events to AG-UI                         | [Agent Stream Protocol](https://agent-foundation-docs.converge.ai/agent-stream-protocol/)     |
| Implement an Environment backend                        | [Environment Provider](https://agent-foundation-docs.converge.ai/agent-environment-provider/) |

## Why Agent Foundation?

- **Pydantic AI native**: keep upstream models, messages, tools, events, output validation, and Agent-loop semantics.
- **Library first**: run the Harness inside the application that already owns identity, policy, persistence, and delivery.
- **Explicit ownership**: distinguish process-local execution, portable continuation state, Environment resources, and durable Host records.
- **Portable Environments**: use the same Harness-facing operations with Direct Local, `agent-envd`, or another EIP-backed provider.
- **Composable behavior**: select capabilities, collaborators, middleware, Skills, delegation, and CodeAct without creating a second Agent framework.
- **Observable boundaries**: consume a canonical Harness stream, usage records, OpenTelemetry signals, or an AG-UI projection.

## Project layout

| Component                 | Distribution or binary      | Source                                                                       |
| ------------------------- | --------------------------- | ---------------------------------------------------------------------------- |
| Agent Harness             | `a13n-harness`              | [`packages/agent-harness`](packages/agent-harness)                           |
| Environment Provider      | `a13n-environment-provider` | [`packages/agent-environment-provider`](packages/agent-environment-provider) |
| Environment daemon client | `a13n-envd-client`          | [`packages/agent-envd-client`](packages/agent-envd-client)                   |
| Environment daemon        | `agent-envd`                | [`crates/agent-envd`](crates/agent-envd)                                     |
| Agent Stream Protocol     | `a13n-stream-protocol`      | [`packages/agent-stream-protocol`](packages/agent-stream-protocol)           |

Python distribution names use the `a13n-` prefix and import packages use underscores, for example `a13n-harness` and `a13n_harness`. The project name remains Agent Foundation.

## Documentation

- [User and integration documentation](https://agent-foundation-docs.converge.ai/)
- [Runnable examples](examples/README.md)
- [Accepted architecture and specifications](spec/README.md)
- [Contribution guide](CONTRIBUTING.md)
- [Development standards](DEVELOPMENT.md)

## Contributing

Issues are the primary venue for proposals, questions, and material design discussion. Pull requests are welcome for specifications, documentation, implementation, tests, examples, and automation.

Read [CONTRIBUTING.md](CONTRIBUTING.md) before making a change. From a configured checkout, use `make check` for fast feedback and `make check-all` before finalizing broad work.

## License

Agent Foundation is licensed under the [Apache License 2.0](LICENSE).

Copyright 2026 Converge AI.
