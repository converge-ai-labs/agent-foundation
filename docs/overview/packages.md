---
title: Package catalog
description: Find PyPI distribution names, Python import paths, native daemons, multi-language SDKs, runnable examples, and release groups.
---

Use this catalog to locate installable packages, repository source directories, and runnable example applications across the Agent Foundation ecosystem. If you are deciding which product fits your architecture, start with [Choose your path](choose-your-path.md).

## Python packages

Install Python distributions by their hyphenated PyPI names (such as `a13n-harness`) and import their modules using underscores (such as `a13n_harness`).

| Distribution           | Repository path                 | Purpose and documentation                                                                                                                                |
| ---------------------- | ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `a13n-harness`         | `packages/a13n-harness`         | [Embeddable Python agent SDK](../a13n-harness/index.md): agent composition, tools, capabilities, state persistence, and environment providers            |
| `a13n-stream-protocol` | `packages/a13n-stream-protocol` | [AG-UI stream translator](../a13n-stream-protocol/index.md): convert Harness observations into typed events with checkpoint replay                       |
| `a13n-harness-ui`      | `packages/a13n-harness-ui`      | [Interactive terminal and browser workbench](../a13n-harness-ui/index.md), also available as an [embeddable Python App](../a13n-harness-ui/embedding.md) |
| `a13n-envd-client`     | `packages/a13n-envd-client`     | [Python EIP client](../a13n-envd/python-client.md) for communicating with Envd Sessions over HTTP, WebSocket, or stdio                                   |
| `a13n-service`         | `packages/a13n-service`         | [Self-hosted multi-tenant agent platform](../a13n-service/index.md) with Console, RBAC, Memory Spaces, Schedules, and durable workers                    |
| `a13n-logging`         | `packages/a13n-logging`         | [Structured logging setup](../a13n-logging/index.md) for libraries and executables                                                                       |

The portable [Environments](../environments/index.md) execution layer ships inside `a13n-harness` and works both inside agent hosts and in standalone Python scripts.

## Native daemon (Envd)

**Envd** (`crates/a13n-envd`) is a Rust execution daemon that exposes file operations, shell commands, background processes, retained output streams, desktop computer use, and device telemetry over the Environment Interface Protocol (EIP).

- For standalone or remote hosts: [Install the daemon](../a13n-envd/installation.md), [configure sandboxing and egress policies](../a13n-envd/configuration.md), and connect with the [Python client](../a13n-envd/python-client.md).
- With Harness and Harness UI: Select the `local_envd` provider for host-local OS sandboxing; Harness UI can automatically acquire the matching co-versioned `a13n-envd` binary.

## Service SDKs and remote CLI

Connect your application to a running Service deployment using the official client libraries in Python, TypeScript, Go, or Rust, or operate workspaces from the terminal with `a13n-service-cli` (published from the Rust SDK repository).

- Follow [SDKs and CLI](../a13n-service/sdks.md) for installation and quickstart code in each language.
- Browse the [Service HTTP API reference](../a13n-service/api-reference/index.md) for complete endpoint schemas.

## Frontend applications

| Repository path                 | Purpose                                                                                                  |
| ------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `frontend/apps/a13n-console`    | Service's browser interface (**Console**) for workspace management, Agent Composer, and run debugging    |
| `frontend/apps/a13n-harness-ui` | Harness UI's browser interface (**WebUI**) for conversations, file trees, Git diffs, and Live voice mode |
| `frontend/packages/a13n-ui`     | Shared React component library, design tokens, and brand assets                                          |
| `frontend/apps/a13n-docs`       | Fumadocs documentation site published from `docs/` and `proto/a13n-service/openapi.json`                 |
| `frontend/apps/a13n-site`       | Public landing page and technical blog                                                                   |

Prebuilt browser assets for Console and WebUI are bundled directly into the `a13n-service` and `a13n-harness-ui` Python wheels and source distributions, so running either application requires no Node.js runtime. To develop the frontend source, follow [`frontend/README.md`](https://github.com/converge-ai-labs/agent-foundation/blob/main/frontend/README.md).

## Runnable example projects

Explore complete, self-contained applications under [`examples/`](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/README.md):

| Example project                                                                                                                 | What it demonstrates                                                                   |
| ------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| [Agent application](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/agent-app/README.md)                | Multi-turn offline streaming, JSON state persistence, and restart recovery             |
| [Environment providers](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/README.md) | Direct Local, Local Envd, and Docker target lifecycles and file/command operations     |
| [Capabilities and plugins](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/plugins/README.md)           | Custom capabilities, Harness plugins, environment provider plugins, and run extensions |
| [Installed provider plugin](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/provider-plugin/README.md)  | Packaging a custom environment provider and discovering it via Python entry points     |
| [MCP App](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/mcp-apps/README.md)                           | Building a stdio MCP server that renders an interactive browser UI widget              |

For additional cloud and container environment scripts, see [Environment runnable examples](../environments/examples.md).

## Release groups and version compatibility

Components publish in coordinated release groups:

| Release group                    | Versioning contract                                                                                                                       |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| **Harness & Stream Protocol**    | Always release together at the exact same version; `a13n-stream-protocol` pins that exact `a13n-harness` version.                         |
| **Envd & `a13n-envd-client`**    | Always release together at the exact same version across Rust binaries, container images, and the Python client.                          |
| **Service, Harness UI, Logging** | Release independently on their own cadence, pinning reviewed Harness-group versions and compatible dependency bounds in package metadata. |

To contribute code or understand repository boundaries, see [Contributing](https://github.com/converge-ai-labs/agent-foundation/blob/main/CONTRIBUTING.md) and the [Repository Model specification](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/repository-model.md).
