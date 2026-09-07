# Agent Foundation (A13N)

[![CI](https://github.com/converge-ai-labs/agent-foundation/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/converge-ai-labs/agent-foundation/actions/workflows/ci.yml)
[![Documentation](https://img.shields.io/badge/docs-agent--foundation-blue)](https://agent-foundation-docs.converge.ai/)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

**Build agents in code, use them locally, or run them as managed services.**

Agent Foundation is a Python-first open-source toolkit for building agents and multi-agent systems on [Pydantic AI](https://ai.pydantic.dev/). Start with a library, run a personal Agent locally, or operate Agents as a managed service without switching execution frameworks.

> Agent Foundation is under active 0.x development. APIs may change between minor releases on the way to the first stable release.

## Choose your path

| You want to...                   | Start with                                                                                 | You get                                                                                            |
| -------------------------------- | ------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| Build Agents into an application | [Agent Harness](https://agent-foundation-docs.converge.ai/agent-harness/) (`a13n-harness`) | A code-first Python library for composing, running, resuming, and observing Agents                 |
| Run Agents on your own machine   | [Agent CLI](packages/agent-ui/README.md) (`a13n-ui`)                                       | A local single-user experience with continuation-backed Sessions and full-terminal interaction     |
| Operate managed Agents           | [Foundation Service](packages/foundation-service/README.md) (`a13n-service`)               | A durable service with APIs, managed definitions, authorization, persistence, and scalable workers |

Agent UI and Foundation Service both build on Agent Harness. `a13n` is short for Agent Foundation, so the managed service distribution is simply `a13n-service`.

## Highlights

- **Pydantic AI native**: use upstream models, messages, tools, events, output validation, and Agent-loop semantics.
- **One foundation, three paths**: start in application code, move to a personal UI, or run a managed service.
- **Portable Environments**: give Agents consistent access to files, commands, processes, and ports across local and isolated backends.
- **State and resume**: preserve conversation state and continue work across process restarts or Host boundaries.
- **Composable behavior**: combine Capabilities, Skills, delegation, CodeAct, middleware, and trusted plugins.
- **Built for observation**: consume typed streams, usage records, OpenTelemetry signals, and AG-UI projections.

## Quick start

The documentation tracks `main` and the next 0.x releases. To work against the current source:

```bash
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
```

Then choose a path from the repository root:

| Path               | Command                                   | Continue with                                                      |
| ------------------ | ----------------------------------------- | ------------------------------------------------------------------ |
| Agent Harness      | `uv sync --locked --package a13n-harness` | [Getting Started](docs/agent-harness/getting-started.md)           |
| Agent CLI          | `make a13n-ui`                            | [Agent CLI guide](packages/agent-ui/README.md)                     |
| Foundation Service | `make dev`                                | [Service development guide](packages/foundation-service/README.md) |
| Runnable examples  | `make examples-check-all`                 | [Examples](examples/README.md)                                     |

Working from source requires Git, Python 3.13, and [`uv`](https://docs.astral.sh/uv/). The Agent UI, Foundation Service, and example commands use Make; Foundation Service development also requires Node.js 24 and Docker. See [CONTRIBUTING.md](CONTRIBUTING.md) for the complete toolchain and validation workflow.

## Documentation

- [User documentation](https://agent-foundation-docs.converge.ai/)
- [Agent Harness getting started](docs/agent-harness/getting-started.md)
- [Runnable examples](examples/README.md)
- [Accepted architecture and specifications](spec/README.md)
- [Development standards](DEVELOPMENT.md)

## Contributing

Bug reports, feature ideas, and design discussions are welcome in [GitHub Issues](https://github.com/converge-ai-labs/agent-foundation/issues). Pull requests are welcome for specifications, documentation, implementation, tests, examples, and automation.

Read [CONTRIBUTING.md](CONTRIBUTING.md) before making a change. From a configured checkout, run `make check` for fast feedback and `make check-all` before finalizing broad work.

## License

Agent Foundation is licensed under the [Apache License 2.0](LICENSE).

Copyright 2026 Converge AI.
