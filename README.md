<p align="center">
  <img src="frontend/packages/a13n-ui/src/brand/a13n-logo.svg" alt="a13n logo" width="128">
</p>

<h1 align="center">Agent Foundation (a13n)</h1>

[![CI](https://github.com/converge-ai-labs/agent-foundation/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/converge-ai-labs/agent-foundation/actions/workflows/ci.yml) [![Documentation](https://img.shields.io/badge/docs-agent--foundation-blue)](https://agent-foundation-docs.converge.ai/) [![Python](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/) [![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

**The open-source, self-hosted foundation for enterprise AI agents.**

Agent Foundation is an open-source platform for building and running your own agent systems. Managed agents, memory, sandboxes, computer use, and durable execution come together in a self-hosted service, ready to integrate into your applications.

- **Build with Service.** Configure agents and connect them to your product through APIs, with resource management, permissions, and execution recovery already in place.
- **Extend with Harness.** Embed the runtime directly and shape its behavior through plugins, custom tools, and providers.
- **Explore with Harness UI.** Try models, tools, and agent configurations in a terminal and web playground—without building an application first.

> Agent Foundation is in active `0.x` development. APIs and configuration may change between minor releases. This README and the documentation site track `main`; check release notes and package metadata when using a published version.

## Try Service locally

Start Service, Console, PostgreSQL, and Redis with **Docker Compose**. The local quickstart creates an administrator and workspace for you; no Python, Node.js, or source build is needed.

Download the [quickstart Compose file](deploy/docker/compose/a13n-service-quickstart.yaml) into an empty directory, then run:

```bash
docker compose -f a13n-service-quickstart.yaml up -d --wait
```

Open **<http://127.0.0.1:8080>** and sign in:

| Email               | Password                    |
| ------------------- | --------------------------- |
| `admin@example.com` | `local-public-password-123` |

**Local trial only.** These credentials are public. The stack listens only on loopback and does not mount your host Docker socket. Do not expose it to a network or use this account for a shared deployment. The source Compose file uses the development image; Service release assets pin it to their release version.

Bring your own model provider API key. In Console, **add a model → create an agent → try it**. The quickstart includes no model or inference credits; plain chat needs no sandbox.

**Continue with the [Service quickstart](docs/a13n-service/get-started.md)** for the guided first conversation, API usage, stop/resume, and troubleshooting. For a shared deployment with your own administrator credentials and optional host Docker environments, use the [deployment guide](deploy/docker/compose/README.md).

## Use Harness UI

For individual work or trusted collaborators, Harness UI offers a terminal agent and browser workbench over the same Harness. Explore repositories, edit files, run commands, and try models, tools, Skills, and environments interactively.

```bash
uv tool install a13n-harness-ui
cd your-repository
a13n-harness-ui          # Terminal
# Or: a13n-harness-ui webui
```

First-use setup connects a model and asks you to choose execution permissions. **Full Control runs as your host account, not in a sandbox.** The browser shares the instance's authority; it is not a multi-tenant Service. Native host file and terminal access is enabled by default; `--no-share-computer` disables those browser features. See [installation](docs/a13n-harness-ui/installation.md), [execution permissions](docs/a13n-harness-ui/environments-and-projects.md#execution-permissions), and [browser access](docs/a13n-harness-ui/webui.md).

## Build on Harness

Harness gives your application reusable agents, typed tools and outputs, streaming observations, portable execution environments, and state you can save and resume. Your application owns persistence, credentials, and recovery policy.

Start with the [Harness quickstart](docs/a13n-harness/getting-started.md), then explore [runnable examples](examples/README.md). Supporting components such as Envd, Stream Protocol, and logging are covered in the [package catalog](docs/packages.md); you do not need to deploy every component.

## Work from source

```bash
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
```

| Work on             | Command                                   | Guide                                                   |
| ------------------- | ----------------------------------------- | ------------------------------------------------------- |
| Harness             | `uv sync --locked --package a13n-harness` | [Getting started](docs/a13n-harness/getting-started.md) |
| Harness UI terminal | `make cli`                                | [Local UI development](dev/harness-ui/README.md)        |
| Harness UI browser  | `make webui`                              | [Local UI development](dev/harness-ui/README.md)        |
| Service and Console | `make dev`                                | [Local Service development](dev/service/README.md)      |
| Documentation       | `make docs-serve`                         | [Contributing](CONTRIBUTING.md)                         |

Source development uses Python 3.13, uv, and Make. Browser builds also need Node.js 24 and the pinned pnpm version; Service development needs Docker. Follow [CONTRIBUTING.md](CONTRIBUTING.md) for the toolchain and checks relevant to your change.

## Contribute

Bug reports, documentation fixes, examples, and focused improvements are welcome. Search [GitHub Issues](https://github.com/converge-ai-labs/agent-foundation/issues) and read the [contribution guide](CONTRIBUTING.md) before starting. Discuss unresolved product or architecture decisions before implementing them.

- [User documentation](https://agent-foundation-docs.converge.ai/)
- [Development standards](DEVELOPMENT.md)
- [Accepted specifications](spec/README.md)
- [Maintainers](MAINTAINERS.md)

## Security

Report suspected vulnerabilities privately to [support@converge.ai](mailto:support@converge.ai), not in public issues or pull requests. See [SECURITY.md](SECURITY.md) for what to include.

## License

Agent Foundation is licensed under the [Apache License 2.0](LICENSE).

Copyright 2026 Converge AI.
