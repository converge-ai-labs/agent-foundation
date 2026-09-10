# Package catalog

Choose a package by the boundary you need, not by a shared name. The repository contains seven Python workspace packages, one Rust workspace crate, five standalone Service client projects, four private frontend manifests, and three independent example projects. Workspace roots, lockfiles, generated protocol artifacts, and vendored references are not additional products.

This catalog follows the source on `main`. It describes implemented scope, not registry availability or a promise that all languages have the same API coverage.

## Python workspace libraries and applications

Python distributions under `packages/` use hyphens; their import names use underscores.

| Distribution and source                                  | Responsibility                                                                                | Documentation owner                                                                                                                                                                                                       |
| -------------------------------------------------------- | --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `a13n-harness` — `packages/a13n-harness`                 | Embedded Agent composition, scoped execution, tools, continuation, and observation            | [Harness](a13n-harness/index.md), with feature guides for [client tools](a13n-harness/client-tools.md), [managed policy](a13n-harness/managed-tools.md), and [model authentication](a13n-harness/model-authentication.md) |
| `a13n-environment` — `packages/a13n-environment`         | Provider-neutral single-target files, commands, processes, state, and lifecycle               | [Environment](a13n-environment/index.md), [Provider configuration](a13n-environment/configuration.md), and [operations](a13n-environment/operations.md)                                                                   |
| `a13n-stream-protocol` — `packages/a13n-stream-protocol` | Harness observations to typed AG-UI events; no transport server                               | [Stream Protocol](a13n-stream-protocol/index.md) and [API/payload reference](a13n-stream-protocol/api-reference.md)                                                                                                       |
| `a13n-harness-ui` — `packages/a13n-harness-ui`           | Terminal product, reusable Python App, foreground HTTP server, and bundled browser foundation | [Harness UI](a13n-harness-ui/index.md), [App embedding](a13n-harness-ui/embedding.md), and [HTTP API](a13n-harness-ui/http-api.md)                                                                                        |
| `a13n-envd-client` — `packages/a13n-envd-client`         | Low-level generated EIP methods, transports, sessions, file transfers, and output readers     | [Python EIP client](a13n-envd/python-client.md); the higher-level Provider contract stays in Environment                                                                                                                  |
| `a13n-service` — `packages/a13n-service`                 | Hosted identity, resources, durable Runs, APIs, workers, and connectivity                     | [Service](a13n-service/index.md), [resource lifecycles](a13n-service/resources.md), [configuration](a13n-service/configuration.md), and [Native API reference](a13n-service/api-reference.md)                             |
| `a13n-logging` — `packages/a13n-logging`                 | Shared namespaced structured logging and executable logging setup                             | [Logging](a13n-logging/index.md)                                                                                                                                                                                          |

Harness, Environment, and Stream Protocol share one exact release version. Harness UI releases independently against a bounded Harness compatibility line. Logging has its own release channel. The Envd Python client is co-versioned with the native daemon, not with Service. Source workspace versions and dependency declarations are not the final published metadata.

## Native daemon

| Crate and source                 | Responsibility                                                                                          | Documentation owner                                                                                                                                                |
| -------------------------------- | ------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `a13n-envd` — `crates/a13n-envd` | EIP daemon for sessions, files, contained command execution, processes, retained output, and transports | [Envd](a13n-envd/index.md), [installation](a13n-envd/installation.md), [configuration](a13n-envd/configuration.md), and [sessions/output](a13n-envd/operations.md) |

The daemon does not execute an Agent. The low-level Python client does not install or launch the executable; the Local Envd Provider and its Host runtime own launch integration. Native executable acquisition in Harness UI is an application-owned convenience, not a requirement for every SDK consumer.

## Standalone Service SDKs and CLI

These projects are outside the root language workspaces. They call Service; they are not alternate implementations of the embedded Harness SDK.

| Package / import                                                                | Source                      | Implemented scope and documentation                                                                                                                                                             |
| ------------------------------------------------------------------------------- | --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Python distribution and import `a13n`                                           | `sdk/python`                | Search client; [Python README](https://github.com/converge-ai-labs/agent-foundation/blob/main/sdk/python/README.md)                                                                             |
| Go module `github.com/converge-ai-labs/agent-foundation/sdk/go`, package `a13n` | `sdk/go`                    | Search client; [Go README](https://github.com/converge-ai-labs/agent-foundation/blob/main/sdk/go/README.md)                                                                                     |
| Rust crate and library `a13n`                                                   | `sdk/rust`                  | Search client; [Rust README](https://github.com/converge-ai-labs/agent-foundation/blob/main/sdk/rust/README.md)                                                                                 |
| TypeScript package and import `@converge.ai/a13n`                               | `sdk/typescript`            | Native API client, binary uploads/downloads, Run SSE, and lifecycle notifications; [TypeScript README](https://github.com/converge-ai-labs/agent-foundation/blob/main/sdk/typescript/README.md) |
| Cargo package and executable `a13n-service-cli`                                 | `sdk/rust/a13n-service-cli` | Help/version scaffold only; no network commands yet; [CLI README](https://github.com/converge-ai-labs/agent-foundation/blob/main/sdk/rust/a13n-service-cli/README.md)                           |

The [Service SDK guide](a13n-service/sdks.md) compares constructors, errors, retry and streaming behavior. Use the [HTTP contracts](a13n-service/http-contracts.md) and [Native API reference](a13n-service/api-reference.md) where a language has no typed operation. Do not substitute an invented CLI command or assume TypeScript coverage exists in every SDK.

## Private frontend workspace

These manifests organize source development. They do not define independently published npm products.

| Manifest name           | Source                          | Owner and scope                                                                                                                                                                                                                                                                                         |
| ----------------------- | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `a13n-frontend`         | `frontend/package.json`         | Private pnpm orchestration; [frontend README](https://github.com/converge-ai-labs/agent-foundation/blob/main/frontend/README.md) owns workspace commands                                                                                                                                                |
| `a13n-console`          | `frontend/apps/a13n-console`    | Service resource management and conversations; [Console user guide](a13n-service/console.md) owns product workflows, [app README](https://github.com/converge-ai-labs/agent-foundation/blob/main/frontend/apps/a13n-console/README.md) owns source setup                                                |
| `a13n-harness-ui-webui` | `frontend/apps/a13n-harness-ui` | Browser authentication/runtime-status foundation bundled in the Python UI distribution; [browser guide](a13n-harness-ui/webui.md) owns current capabilities, [app README](https://github.com/converge-ai-labs/agent-foundation/blob/main/frontend/apps/a13n-harness-ui/README.md) owns Vite development |
| `a13n-ui`               | `frontend/packages/a13n-ui`     | Shared React components, tokens, brand assets, and private showcase; [shared UI README](https://github.com/converge-ai-labs/agent-foundation/blob/main/frontend/packages/a13n-ui/README.md) owns source usage                                                                                           |

Console is not the Harness UI browser. Service does not itself serve Console assets. The Harness UI browser is not a complete chat or management workbench; its Python App and HTTP API expose more functionality than the current browser interface. Its compiled assets ship inside the Python wheel and sdist, without requiring Node.js for installed runtime use.

## Runnable example projects

Example projects have independent manifests and lockfiles for realistic packaging. They are not production release packages. Keep runnable instructions beside their code rather than maintaining duplicate site manuals.

| Example distribution       | Source and runnable instructions                                                                                                                                                                           | Related guide                                                                               |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `a13n-agent-app-example`   | [`examples/agent-app`](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/agent-app/README.md) — application-owned Agent and feature composition                                      | [Harness hosting](a13n-harness/hosting.md) and [Capabilities](a13n-harness/capabilities.md) |
| `a13n-environment-example` | [`examples/environment-provider`](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/README.md) — custom Provider implementation and registration                | [Providers and runtime](a13n-environment/providers.md)                                      |
| `a13n-plugin-examples`     | [`examples/plugins`](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/plugins/README.md) — Capability, Environment, Environment Run Extension, and Harness Plugin integration paths | [Plugins and Extensions](a13n-harness/plugins.md)                                           |

For complete built-in Provider runs, use [Environment examples](a13n-environment/examples.md). These are distinct from the custom-Provider example project.

## Where information belongs

- **User workflows, configuration, operational limits, and public integration examples:** the relevant component section of this site.
- **Source setup, package-local commands, and runnable example instructions:** package and example READMEs, linked above.
- **Repository-wide development and validation:** [Contributing](https://github.com/converge-ai-labs/agent-foundation/blob/main/CONTRIBUTING.md).
- **Accepted ownership, lifecycle, protocol, and release contracts:** [specifications](https://github.com/converge-ai-labs/agent-foundation/tree/main/spec), starting with the [repository model](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/repository-model.md).

A generated API or configuration reference supplements a task guide; it does not explain an entire workflow by itself. Internal helpers, generated wire files, migration history, and shared private components remain documented through their owning boundary instead of acquiring parallel user manuals.
