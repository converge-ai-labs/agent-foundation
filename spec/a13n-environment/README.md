# Environment Library

`a13n-environment` provides single-environment management and execution capabilities for Python applications. It owns Environment Provider definitions and implementations, including native backends and Envd-backed Providers using EIP, together with their portable contracts. Host callers own selection, authorization, persistence, and lifecycle policy; Harness callers own their use of execution scopes. These are caller roles, independent of any specific application package.

| Document                                           | Owns                                                                                                                                        |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| [Architecture](00-overview.md)                     | Package boundaries, dependency direction, and the management-to-execution flow                                                              |
| [Environment Contract](01-environment-contract.md) | Definitions, management primitives, Environment connectors, Environment execution scopes, portable state, operations, and failure semantics |
| [Providers](02-providers.md)                       | Built-in target, transport, operation, and cleanup guarantees                                                                               |

Start with the overview, then the shared contract and the relevant provider. [Envd](../a13n-envd/README.md) owns EIP and daemon execution. Application-specific integration, orchestration, and persistence are defined by each consumer outside this subsystem.

These documents own the shared Environment contract. Other subsystems summarize its use and link here rather than defining another provider lifecycle.
