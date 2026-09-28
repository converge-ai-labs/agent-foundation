# Package catalog

Find the library, application, or example for your integration. Python distributions use hyphens (`a13n-harness`); imports use underscores (`a13n_harness`).

## Python packages

| Distribution           | Source                          | Purpose and guide                                                                                                               |
| ---------------------- | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `a13n-harness`         | `packages/a13n-harness`         | [Agent composition and execution](a13n-harness/index.md), tools, state, and providers                                           |
| `a13n-stream-protocol` | `packages/a13n-stream-protocol` | [Convert Harness observations to AG-UI events](a13n-stream-protocol/index.md)                                                   |
| `a13n-harness-ui`      | `packages/a13n-harness-ui`      | [Terminal and browser workbench](a13n-harness-ui/index.md), also available as an [embeddable App](a13n-harness-ui/embedding.md) |
| `a13n-envd-client`     | `packages/a13n-envd-client`     | [Python EIP client](a13n-envd/python-client.md) for sessions, files, processes, and output                                      |
| `a13n-service`         | `packages/a13n-service`         | [Managed-agent runtime](a13n-service/index.md) with identity, resources, and durable execution                                  |
| `a13n-logging`         | `packages/a13n-logging`         | [Structured logging](a13n-logging/index.md) for libraries and applications                                                      |

[Environments](environments/index.md) is part of Harness and can also be used without an agent.

## Native daemon

`crates/a13n-envd` builds the **Envd** daemon. It provides sessions, files, commands, processes, and retained output through EIP. See [installation](a13n-envd/installation.md), [configuration](a13n-envd/configuration.md), and the [Python client](a13n-envd/python-client.md).

Harness's Local Envd provider connects the daemon to an Environment. Harness UI can acquire a matching executable automatically when that provider is selected.

## Service SDKs and CLI

Service clients live in independent Python, TypeScript, Go, and Rust repositories. The Rust repository also supplies `a13n-service-cli`. They call the Service HTTP API; use Harness to embed agent execution directly.

[SDKs and CLI](a13n-service/sdks.md) links to each client's installation instructions and examples. The [HTTP reference](a13n-service/api-reference.md) describes this repository's Service API.

## Frontend source

| Source                          | Purpose                                                     |
| ------------------------------- | ----------------------------------------------------------- |
| `frontend/apps/a13n-console`    | Service's browser interface for resources and conversations |
| `frontend/apps/a13n-harness-ui` | Harness UI's interactive browser workbench                  |
| `frontend/packages/a13n-ui`     | Shared React components, design tokens, and brand assets    |

Console ships with Service; the Harness UI browser ships with the Python UI distribution. Both are bundled in their wheel and sdist, so installed users need no Node.js. For frontend development, follow the [frontend README](https://github.com/converge-ai-labs/agent-foundation/blob/main/frontend/README.md).

## Runnable example projects

Each project has its own dependencies, tests, and run instructions. Start with its README:

| Example                                                                                                                         | Demonstrates                                                                    |
| ------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| [Agent application](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/agent-app/README.md)                | Offline streaming turns, saved state, and restart recovery                      |
| [Environment providers](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/README.md) | Direct Local, Local Envd, and Docker lifecycles                                 |
| [Capabilities and plugins](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/plugins/README.md)           | Custom capabilities, Harness plugins, Environment providers, and run extensions |
| [Installed provider plugin](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/provider-plugin/README.md)  | Package an Environment provider and load its installed entry point              |
| [MCP App](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/mcp-apps/README.md)                           | A stdio MCP server with an interactive browser counter                          |

The [examples index](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/README.md) lists run commands and prerequisites. [Environment examples](environments/examples.md) covers additional built-in providers.

## Releases

| Release group                | Version relationship                                                      |
| ---------------------------- | ------------------------------------------------------------------------- |
| Harness and Stream Protocol  | Same release version; Stream Protocol requires that exact Harness version |
| Envd and its Python client   | Same release version                                                      |
| Harness UI, Service, Logging | Independent releases; package metadata declares compatible dependencies   |

The frontend ships with its owning application. Example projects are separate from production releases. Source workspace versions are development placeholders; use published package metadata when selecting deployed versions.

For contributor setup, see [Contributing](https://github.com/converge-ai-labs/agent-foundation/blob/main/CONTRIBUTING.md). For repository and release contracts, see the [repository model](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/repository-model.md).
