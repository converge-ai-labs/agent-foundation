---
title: Package catalog
description: Distribution names, imports, source locations, runnable examples, and release groups.
---

Use this catalog to find a component's installable distribution, source, and examples. Start with [Choose your path](choose-your-path.md) if you are still deciding between embedded execution, Service, and Harness UI.

## Python packages

Install distributions with hyphenated names such as `a13n-harness`; import their modules with underscores, as in `a13n_harness`.

| Distribution           | Source                          | Purpose and guide                                                                                                                     |
| ---------------------- | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `a13n-harness`         | `packages/a13n-harness`         | [Agent composition and execution](../a13n-harness/index.md), tools, state, and providers                                              |
| `a13n-stream-protocol` | `packages/a13n-stream-protocol` | [Convert Harness observations to AG-UI events](../a13n-stream-protocol/index.md)                                                      |
| `a13n-harness-ui`      | `packages/a13n-harness-ui`      | [Terminal and browser workbench](../a13n-harness-ui/index.md), also available as an [embeddable App](../a13n-harness-ui/embedding.md) |
| `a13n-envd-client`     | `packages/a13n-envd-client`     | [Python EIP client](../a13n-envd/python-client.md) for Envd Sessions, files, processes, and output                                    |
| `a13n-service`         | `packages/a13n-service`         | [Managed-agent runtime](../a13n-service/index.md) with identity, resources, and durable execution                                     |
| `a13n-logging`         | `packages/a13n-logging`         | [Structured logging](../a13n-logging/index.md) for libraries and applications                                                         |

For portable file and command access, use [Environments](../environments/index.md) from Harness. The same providers work in ordinary applications and agent hosts.

## Native daemon

Use **Envd** to access files, commands, processes, retained output, and computer operations through EIP Sessions. Its native source lives in `crates/a13n-envd`. For a standalone deployment, [install the daemon](../a13n-envd/installation.md), [configure it](../a13n-envd/configuration.md), and connect with the [Python client](../a13n-envd/python-client.md).

In Harness, select the Local Envd provider to use the daemon as an environment. Harness UI can acquire the matching executable automatically for that provider.

## Service SDKs and CLI

Choose a Python, TypeScript, Go, or Rust client to call the Service HTTP API. Each client has its own repository; the Rust repository also supplies `a13n-service-cli`.

Follow [SDKs and CLI](../a13n-service/sdks.md) to install a client and run its examples. Use the [HTTP reference](../a13n-service/api-reference/index.md) to inspect the Service API.

## Frontend source

| Source                          | Purpose                                                     |
| ------------------------------- | ----------------------------------------------------------- |
| `frontend/apps/a13n-console`    | Service's browser interface for resources and conversations |
| `frontend/apps/a13n-harness-ui` | Harness UI's WebUI                                          |
| `frontend/packages/a13n-ui`     | Shared React components, design tokens, and brand assets    |
| `frontend/apps/a13n-docs`       | Documentation site built from `docs/`                       |
| `frontend/apps/a13n-site`       | Public landing page                                         |

Install Service to get Console, or `a13n-harness-ui` to get WebUI. Their built browser assets ship in the owning application's wheel and sdist, so running an installed application needs no Node.js. To change those assets, follow the [frontend development guide](https://github.com/converge-ai-labs/agent-foundation/blob/main/frontend/README.md).

## Runnable example projects

Choose the example closest to your integration, then follow its README for dependencies, execution, and tests:

| Example                                                                                                                         | Demonstrates                                                                    |
| ------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| [Agent application](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/agent-app/README.md)                | Offline streaming turns, saved state, and restart recovery                      |
| [Environment providers](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/README.md) | Direct Local, Local Envd, and Docker lifecycles                                 |
| [Capabilities and plugins](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/plugins/README.md)           | Custom capabilities, Harness plugins, environment providers, and run extensions |
| [Installed provider plugin](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/provider-plugin/README.md)  | Package an environment provider and load its installed entry point              |
| [MCP App](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/mcp-apps/README.md)                           | A stdio MCP server with an interactive browser counter                          |

Use the [examples index](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/README.md) to compare prerequisites and run commands. For more built-in providers, continue to [Environment examples](../environments/examples.md).

## Releases

Select versions by release group:

| Release group                | Version relationship                                                      |
| ---------------------------- | ------------------------------------------------------------------------- |
| Harness and Stream Protocol  | Same release version; Stream Protocol requires that exact Harness version |
| Envd and its Python client   | Same release version                                                      |
| Harness UI, Service, Logging | Independent releases; package metadata declares compatible dependencies   |

Choose deployed versions from published package metadata. Source-tree versions are development placeholders, and example projects stay outside production releases.

To develop a package, start with [Contributing](https://github.com/converge-ai-labs/agent-foundation/blob/main/CONTRIBUTING.md). The [repository model](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/repository-model.md) defines package ownership and release relationships.
