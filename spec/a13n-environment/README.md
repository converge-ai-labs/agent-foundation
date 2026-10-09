# Environment Library

`a13n-environment` provides single-environment management and execution capabilities for Harness and other Python consumers. It owns Environment Provider definitions and implementations, including native backends and Envd-backed Providers using EIP, together with their portable contracts. Hosts own selection, authorization, persistence, and lifecycle policy.

| Document                                           | Owns                                                                                                                                        |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| [Architecture](00-overview.md)                     | Package boundaries, dependency direction, and the management-to-execution flow                                                              |
| [Environment Contract](01-environment-contract.md) | Definitions, management primitives, Environment connectors, Environment execution scopes, portable state, operations, and failure semantics |
| [Providers](02-providers.md)                       | Built-in target, transport, operation, and cleanup guarantees                                                                               |

Start with the overview, then the shared contract and the relevant provider. [Harness Environment Integration](../a13n-harness/08-environment-integration.md) owns mounts and model-facing behavior. [Service Environments](../a13n-service/06-environments.md) and [Harness UI Environments](../a13n-harness-ui/04-projects-threads-and-environments.md) own their respective durable state and lifecycle policy. [Envd](../a13n-envd/README.md) owns EIP and daemon execution.

These documents own the shared Environment contract. Other subsystems summarize its use and link here rather than defining another provider lifecycle.
