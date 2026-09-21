# a13n-envd Specifications

## Overview

`a13n-envd` is a client-neutral device daemon for files, shells, processes, output and transfers. One daemon normally serves a machine or outer sandbox. Its trusted Host connection carries multiple independent Sessions, each with a Host-selected working directory and Session-owned resources.

HTTP, reverse WebSocket and stdio share EIP 0.1 semantics. Device connection lifetime is independent of individual Sessions. The Host supplies security through the account or outer sandbox used to launch the entire daemon; cwd is not filesystem or shell isolation. Periodic and high-water collection reclaim abandoned Sessions and completed history under generous finite resource bounds.

Harness is one consumer through the provider-neutral [Environment integration](../a13n-harness/08-environment-integration.md). The [Environment Providers](../a13n-harness/08a-environment-providers.md) construct fresh adapters/Sessions while Host runtimes can reuse Device connections. Direct Local, native Docker and E2B do not require envd. The low-level client does not discover, install or launch daemons.

## Document Catalog

| Document                                                                                   | Owning contract                                                                |
| ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------ |
| [00-overview.md](00-overview.md)                                                           | Architecture, ownership, main flow and invariants                              |
| [01-daemon-lifecycle-and-configuration.md](01-daemon-lifecycle-and-configuration.md)       | Device identity, generation, bootstrap, aggregate limits and shutdown          |
| [02-eip-protocol.md](02-eip-protocol.md)                                                   | Device/Session controls, method catalog, selectors, evidence and compatibility |
| [03-transports-and-sessions.md](03-transports-and-sessions.md)                             | Authentication, multiplexing, framing and disconnect semantics                 |
| [04-resource-operations.md](04-resource-operations.md)                                     | Device paths, directory discovery, file/search/port operations and transfers   |
| [05-command-and-process-execution.md](05-command-and-process-execution.md)                 | Command start, process status, control and native cleanup                      |
| [06-output-retention.md](06-output-retention.md)                                           | Separate raw stdout/stderr, offsets, bounds and release                        |
| [07-execution-isolation.md](07-execution-isolation.md)                                     | Host boundary, controlled Session egress and native cleanup limits             |
| [08-protocol-source-client-and-generation.md](08-protocol-source-client-and-generation.md) | IDL, generated Rust/Python surfaces, client and release ownership              |
| [09-resource-lifetime-and-reclamation.md](09-resource-lifetime-and-reclamation.md)         | Session inactivity, disconnect grace, history and pressure collection          |

## Reading Paths

- Architecture: `00`, `01`, `02`, `09`.
- Provider/client integration: `02`, `03`, `08` and the Environment contracts.
- Native operations: `04`, `05`, `06`, `07`.
- Lifetime and failure review: `03`, `05`, `06`, `09`.

## Ownership Rules

- The Host owns Device registration/authentication, working-directory selection, Provider selection, launch security and durable state.
- Envd owns Session routing, native operations and cleanup, bounded output/transfer storage and aggregate accounting.
- A Session owns its processes, output, transfers and operation evidence. No resource import or independent resource-lease system exists.
- The carrier owns message delivery, not native command lifetime. Same-Session reattachment is bounded and never implies mutation replay or transfer resume.
- Harness owns Run-local mounts, action ceilings and model-facing references, not Devices or EIP sockets.
- Workspace files survive Session cleanup. Session history is temporary, reclaimable and never a durable artifact guarantee.

## Specification Conventions

Python-like shapes are conceptual unless explicitly identified as serialized EIP JSON. EIP uses snake_case fields and one JSON-RPC request/response per message; file bytes use the raw data plane. A receipt records observed native effects, not Host completion. Device, generation, Session, operation and model-facing Run references are distinct identities.

These contracts require a coordinated breaking implementation. They do not make the previous singleton client, existing generated artifacts or downstream Service integration compatible without adaptation. Service/Console product changes remain separately scoped.
