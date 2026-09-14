# Agent Foundation (A13N)

[![CI](https://github.com/converge-ai-labs/agent-foundation/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/converge-ai-labs/agent-foundation/actions/workflows/ci.yml) [![Documentation](https://img.shields.io/badge/docs-agent--foundation-blue)](https://agent-foundation-docs.converge.ai/) [![Python](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/) [![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

**Build agents in code, use them locally, or run them as managed services.**

Agent Foundation is a Python-first open-source toolkit for building agents and multi-agent systems on [Pydantic AI](https://ai.pydantic.dev/). Start with a library, run a personal Agent locally, or operate Agents as a managed service without switching execution frameworks.

> Agent Foundation is under active 0.x development. APIs may change between minor releases on the way to the first stable release.

## Choose your path

| You want to...                   | Start with                                                                                | You get                                                                                            |
| -------------------------------- | ----------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Build Agents into an application | [Agent Harness](https://agent-foundation-docs.converge.ai/a13n-harness/) (`a13n-harness`) | A code-first Python library for composing, running, resuming, and observing Agents                 |
| Run Agents on your own machine   | [Harness UI](packages/a13n-harness-ui/README.md) (`a13n-harness-ui`)                      | A local single-user experience with continuation-backed Sessions and full-terminal interaction     |
| Operate managed Agents           | [a13n Service](packages/a13n-service/README.md) (`a13n-service`)                          | A durable service with APIs, managed definitions, authorization, persistence, and scalable workers |

Harness UI and a13n Service both embed Agent Harness, but they own different lifecycles:

| Direct Agent use                                                                                | Managed Agent use                                                                                                               |
| ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| An application embeds `a13n-harness`, or a person runs `a13n-harness-ui`.                       | A client uses Console, Native HTTP, or an implemented Service SDK operation.                                                    |
| The application or UI owns execution, configuration, continuation storage, and recovery policy. | The Service owns managed resources and revisions, durable acceptance, scheduling, Runs and Attempts, permissions, and recovery. |
| Models and execution Environments may still be remote.                                          | The Service may still run on the same machine as its client.                                                                    |

`a13n` identifies Service client packages, not an umbrella package or another Agent execution engine. [Console](docs/a13n-service/console.md) is the repository's Service management browser application. [SDK coverage](docs/a13n-service/sdks.md) differs by language: TypeScript covers Native HTTP and streams; Python, Go, and Rust currently cover Web Provider management. The companion `a13n-service-cli` currently exposes help/version only.

Harness UI interaction is provided by the terminal CLI. Its optional `a13n-harness-ui webui` server retains the HTTP API; the bundled browser page provides authentication, status, and version information, not browser chat or Service management. See [the browser-server guide](docs/a13n-harness-ui/webui.md).

## Highlights

- **Pydantic AI native**: use upstream models, messages, tools, events, output validation, and Agent-loop semantics.
- **One foundation, three paths**: start in application code, move to a personal UI, or run a managed service.
- **Portable Environments**: give Agents consistent access to files, commands, processes, and ports across local and isolated backends.
- **State and resume**: preserve conversation state and continue work across process restarts or Host boundaries.
- **Composable behavior**: combine Capabilities, Skills, delegation, CodeAct, middleware, and trusted plugins.
- **Built for observation**: consume typed streams, usage records, OpenTelemetry signals, and AG-UI projections.

## Quick start

### Use Harness UI

Install the published CLI with [`uv`](https://docs.astral.sh/uv/getting-started/installation/), then launch it in your project:

```bash
uv tool install a13n-harness-ui
cd your-repository
a13n-harness-ui
```

For a shorter command, add `alias anui='a13n-harness-ui'` to your Bash or Zsh configuration. Update whenever you choose with `a13n-harness-ui update` (or `anui update`). See the [Harness UI README](packages/a13n-harness-ui/README.md#install-and-run) for setup, PATH help, and update behavior.

### Work from Source

The documentation tracks source `main`; match published packages to their release contracts. To work against the current source:

```bash
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
```

Then choose a path from the repository root:

| Path              | Command                                   | Continue with                                                |
| ----------------- | ----------------------------------------- | ------------------------------------------------------------ |
| Agent Harness     | `uv sync --locked --package a13n-harness` | [Getting Started](docs/a13n-harness/getting-started.md)      |
| Harness UI        | `make a13n-harness-ui`                    | [Harness UI guide](packages/a13n-harness-ui/README.md)       |
| a13n Service      | `make dev`                                | [Service development guide](packages/a13n-service/README.md) |
| Runnable examples | `make examples-check-all`                 | [Examples](examples/README.md)                               |

Working from source requires Git, Python 3.13, and [`uv`](https://docs.astral.sh/uv/). The Harness UI, a13n Service, and example commands use Make; a13n Service development also requires Node.js 24 and Docker. See [CONTRIBUTING.md](CONTRIBUTING.md) for the complete toolchain and validation workflow.

## Documentation

- [User documentation](https://agent-foundation-docs.converge.ai/)
- [Agent Harness getting started](docs/a13n-harness/getting-started.md)
- [Runnable examples](examples/README.md)
- [Accepted architecture and specifications](spec/README.md)
- [Development standards](DEVELOPMENT.md)

## Contributing

Bug reports, feature ideas, and design discussions are welcome in [GitHub Issues](https://github.com/converge-ai-labs/agent-foundation/issues). Pull requests are welcome for specifications, documentation, implementation, tests, examples, and automation.

Read [CONTRIBUTING.md](CONTRIBUTING.md) before making a change. From a configured checkout, run `make check` for fast feedback and `make check-all` before finalizing broad work.

## License

Agent Foundation is licensed under the [Apache License 2.0](LICENSE).

Copyright 2026 Converge AI.
