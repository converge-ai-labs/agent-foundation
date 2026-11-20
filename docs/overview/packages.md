---
title: Package catalog
description: Comprehensive directory of distribution packages, source trees, runtime clients, and official example applications.
---

Explore installable packages, client libraries, native daemons, and reference architectures across the Agent Foundation ecosystem. If you are deciding on the right integration architecture, start with [Choose your path](choose-your-path.md).

## Python packages

All core libraries are packaged for modern Python (3.13+) and published using canonical hyphenated distribution names. When importing into your code, use underscores:

| Distribution | Source path | Primary role & documentation |
| :--- | :--- | :--- |
| `a13n-harness` | `packages/a13n-harness` | [Core agent composition and execution engine](../a13n-harness/index.md), state management, tools, and capabilities |
| `a13n-stream-protocol` | `packages/a13n-stream-protocol` | [Event stream translator](../a13n-stream-protocol/index.md) converting Harness internal observations to typed AG-UI specifications |
| `a13n-harness-ui` | `packages/a13n-harness-ui` | [Interactive developer workbench](../a13n-harness-ui/index.md) providing terminal TUI, WebUI, and embeddable App interfaces |
| `a13n-envd-client` | `packages/a13n-envd-client` | [Python EIP client](../a13n-envd/python-client.md) for interacting with Envd daemons, sandboxes, and file systems |
| `a13n-service` | `packages/a13n-service` | [Distributed agent platform](../a13n-service/index.md) providing multi-tenant workspaces, durable execution, and management APIs |
| `a13n-logging` | `packages/a13n-logging` | [Structured logging framework](../a13n-logging/index.md) tailored for distributed agent workflows and process configurations |

For uniform file system and process access across local, Docker, and cloud targets, integrate [Environments](../environments/index.md) through `a13n-harness`.

## Native daemon

**Envd** is a high-performance native daemon implemented in Rust (`crates/a13n-envd`). It provides sandboxed process management, desktop computer automation, and network egress policy enforcement over the Environment Inter-Process (EIP) protocol.

- Standalone deployment: [Install the binary](../a13n-envd/installation.md), [review configuration](../a13n-envd/configuration.md), and connect using the [Python client](../a13n-envd/python-client.md).
- Harness integration: Select the `local_envd` provider in your environment configuration to leverage local sandboxes automatically.

## Service SDKs and CLI

Official language SDKs and command-line tools enable seamless programmatic interaction with Service deployments:

- **Python SDK:** `a13n-service-sdk`
- **TypeScript / Node.js SDK:** `@a13n/service-sdk`
- **Go SDK:** `github.com/converge-ai-labs/a13n-service-go`
- **Rust SDK & CLI:** `a13n-service-rs` (which distributes the `a13n-service-cli` binary)

Consult the [SDKs and CLI guide](../a13n-service/sdks.md) for installation commands and integration patterns, or reference the [HTTP API documentation](../a13n-service/api-reference/index.md).

## Frontend source

Prebuilt frontend bundles are packaged directly inside their corresponding Python distributions, ensuring that running Service or Harness UI requires zero Node.js runtime dependencies:

| Package / Application | Directory | Purpose |
| :--- | :--- | :--- |
| `a13n-console` | `frontend/apps/a13n-console` | Modern web console for managing Service workspaces, agents, and observability |
| `a13n-harness-ui` | `frontend/apps/a13n-harness-ui` | Collaborative WebUI for Harness UI workbench |
| `a13n-ui` | `frontend/packages/a13n-ui` | Shared React component library, design tokens, and branding assets |
| `a13n-docs` | `frontend/apps/a13n-docs` | Documentation portal built with Fumadocs |
| `a13n-site` | `frontend/apps/a13n-site` | Public showcase landing site |

Frontend contributors can follow the [Frontend Development Guide](https://github.com/converge-ai-labs/agent-foundation/blob/main/frontend/README.md) to run and test web assets locally.

## Runnable example projects

Explore fully functional reference implementations located under `examples/`:

| Example project | Core architecture demonstrated |
| :--- | :--- |
| [Agent Application](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/agent-app/README.md) | Offline event streaming, turn-based state persistence, and process restart recovery |
| [Environment Providers](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/README.md) | Direct Local, Local Envd, and containerized Docker execution lifecycles |
| [Capabilities & Plugins](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/plugins/README.md) | Custom agent capabilities, Harness middleware plugins, and execution extensions |
| [Provider Plugin](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/provider-plugin/README.md) | Packaging custom environment providers via Python entry-point discovery |
| [MCP App](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/mcp-apps/README.md) | Interactive client tools and rich browser UI widgets rendered through stdio MCP |

## Releases

Component releases are grouped logically according to their operational and binary compatibility:

- **Harness & Stream Protocol:** Released under unified, exact version alignment.
- **Envd & Python Client:** Maintained and published together under shared protocol versioning.
- **Service, Harness UI & Logging:** Versioned independently; each package explicitly declares its supported compatibility ranges.

For detailed release procedures and dependency constraints, see [Contributing Releases](https://github.com/converge-ai-labs/agent-foundation/blob/main/CONTRIBUTING.md#releases) and the [Repository Architecture Model](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/repository-model.md).
