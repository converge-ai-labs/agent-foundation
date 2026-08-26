# agent-envd Specifications

## Overview

This directory defines `agent-envd`, the client-neutral Environment host and data-plane daemon for daemon-governed local, sandboxed, and remote resources. One daemon serves one user and one Environment for one process generation. It implements EIP with JSON-RPC control, correlated raw file transfer, bounded file/search/port operations, command/process ownership, command output, and side-effect evidence.

The Harness is one requester through its provider-neutral [`Environment`](../agent-harness/08-environment-integration.md) abstraction. The shared [Environment Provider package](../agent-environment-provider/README.md) manages Direct Local, Local Envd, Docker, and E2B resources and supplies fresh Direct Local or EIP attachments. Direct Local does not require envd; Local Envd is the required-isolation local sandbox over one exact Host-resolved executable. Other trusted consumers can use the generated low-level client independently, but that client never discovers, downloads, installs, or launches a daemon.

EIP uses trusted stdio, Host-dialed HTTP, or outbound reverse WebSocket. In every profile the low-level client/control service remains the requester and envd remains the responder. Only HTTP binds an inbound EIP listener, and it exposes dedicated authenticated control and streaming-transfer resources rather than a generic HTTP, browser, health, file-download, or upload API. Browser users remain behind product authentication and gateway policy and never receive envd credentials, session selectors, or raw transfer handles.

Command isolation defaults to fail-closed required mode on Linux, macOS, and Windows. Linux uses bubblewrap, macOS uses Seatbelt, and Windows combines AppContainer/restricted capabilities plus capability-specific filesystem/network ACL projection with Job Object process-tree ownership. A Job Object alone is not a sandbox. On macOS, Seatbelt confinement is inherited while native cleanup manages the initial process group rather than claiming Linux- or Windows-style adversarial whole-tree ownership; a Host requiring strict teardown supplies a disposable outer Environment. A provider whose outer VM/container/sandbox already owns containment can explicitly select disabled mode; this disables only envd's inner command containment.

Filesystem authority consists only of trusted configured mounts after resource-layer subtraction of envd protected runtime roots. A deliberate whole-filesystem root is an ordinary operator mount, not a session-selectable server mode, and must pass that subtraction independently from command isolation. Exact `available_methods` reports callable support independently per JSON-RPC method; typed `execution_features` reports optional command-limit, per-command-network, and signal-action semantics without reintroducing capability families.

## Document Catalog

| Document                                                                                   | Owning contract                                                                                                                                     |
| ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md)                                                           | Subsystem position, architecture, boundaries, end-to-end flow, and stable principles                                                                |
| [01-daemon-lifecycle-and-configuration.md](01-daemon-lifecycle-and-configuration.md)       | Trusted configuration, Environment identity/generation, private runtime, outbound connector, readiness, ownership, drain, and observability         |
| [02-eip-protocol.md](02-eip-protocol.md)                                                   | JSON-RPC, initialization, exact method availability, operation-ID replay, relative timeouts, transfers, selectors, receipts, errors, and versioning |
| [03-transports-and-sessions.md](03-transports-and-sessions.md)                             | Trusted stdio, Host-dialed HTTP, outbound reverse WebSocket, raw transfer mapping, authentication, and session lifetime                             |
| [04-resource-operations.md](04-resource-operations.md)                                     | Configured mounts, paths, bounded text/search, raw transfer, complete-candidate publication, mutations, and ports                                   |
| [05-command-and-process-execution.md](05-command-and-process-execution.md)                 | Typed executable selection, transactional spawn, process records, signaling, foreground/background lifecycle, and cleanup                           |
| [06-output-retention.md](06-output-retention.md)                                           | Generation-private command-output spool, stdout/stderr references, explicit offsets, limits, and release                                            |
| [07-execution-isolation.md](07-execution-isolation.md)                                     | Required/disabled posture, Linux bubblewrap, macOS Seatbelt, Windows AppContainer/ACL/Job, network policy, probes, and posture                      |
| [08-protocol-source-client-and-generation.md](08-protocol-source-client-and-generation.md) | Canonical IDL, generated Rust/Python surfaces, client runtime boundary, executable installation, conformance, and co-release contract               |

## Reading Paths

### Understand agent-envd

Read `00`, `01`, and `02`, then `03` for carriers, `04` through `07` for resource/security semantics, and `08` for protocol realization.

### Integrate an Environment provider

Read `00`, `01`, `02`, `03`, and `08`, then [Harness Environment Integration](../agent-harness/08-environment-integration.md). A provider owns provisioning, envd bootstrap, short-lived attachment-token issuance and protected-file delivery, and outer teardown. Every daemon-backed operation uses the same generated client and EIP contract.

### Integrate another trusted EIP requester

Read `00`, `02`, `03`, `04`, and `08`. A control service uses the low-level client while retaining its own caller identity, tenant routing, product policy, and durable state. A browser-facing gateway keeps attachment credentials, operation IDs, process/output selectors, and transfer handles server-side unless another accepted product contract projects a safe logical reference.

### Implement files, commands, or processes

Read `02` through `08`. File transfer spans protocol control, carrier frames, and resource commit semantics. Retained output is a separate generation-owned spool. Every command path must follow `07` and the shared transactional execution owner.

### Review security or lifetime

Read `01`, `02`, `03`, `04`, `06`, and `07` together with [Harness Security, Compatibility, and Trade-offs](../agent-harness/15-security-compatibility-and-tradeoffs.md).

## Authority Rules

- The Host owns provider selection, lifecycle policy, current credentials, control-service routing, attachment-token issuance/protected-file delivery, and optional resource-state persistence; the Environment Provider package performs selected lifecycle operations and issues fresh runtime attachments.
- The Harness exhaustively adapts fresh runtime attachments and owns multi-Environment routing and provider-neutral operations. It does not own native resources, provider lifecycle, EIP session sources, command records, or native process targets.
- EIP owns method payloads, operation-ID replay/cancellation/receipt identity, relative timeout semantics, transfers, exact method availability, selectors, errors, and command-output reference/offset/completion semantics.
- The carrier authenticates or establishes the trusted peer. Transport identity never comes from ordinary EIP params.
- Envd owns configured mounts, path enforcement, file transfers, operation evidence, command records, backend-managed native targets, command-output spool, finite capacity, and required native isolation.
- An outer provider owns child containment only when envd isolation is explicitly disabled. Platform detection or failed probing never chooses that mode.
- Configured mounts are the only EIP filesystem roots. A broad root remains trusted configuration and required isolation still protects envd control state.
- Sessions own readers, pre-handoff writers, and binary attachments. Accepted operations, processes, receipts, and output references are daemon-generation-owned.
- A descriptor, mount ID, handle, output reference, operation ID, or receipt grants no authority by possession. Every use repeats current generation, kind, exact-method, lifecycle, and policy checks.
- Native files can outlive envd. Operation/process/output/transfer/spool state is volatile and never restored after restart. Valid process/output records remain until explicit release or generation end. A crash-left destination-local candidate is ordinary native state, not a resumable writer.

## Specification Conventions

- Python-like schemas are conceptual unless explicitly identified as serialized EIP JSON.
- EIP uses `snake_case` fields and one JSON-RPC request/response per control message. Batches and notifications are unsupported.
- Raw file content uses only the correlated binary data plane.
- “Session” means one initialized EIP protocol session, distinct from a WebSocket carrier, Harness run, Host execution, or provider lifecycle session.
- “Initial command” means the requested executable, never a sandbox helper or supervisor.
- “Operation ID” identifies one semantic operation for active cancellation and, when its method retains terminal evidence, for replay and receipt lookup.
- “Receipt” records envd-observed facts and never implies Host durable completion.
- Identifiers, mounts, handles, and references are selectors, not bearer credentials.
- An `expires_at` on a session-scoped file transfer is an observed server timestamp, not a caller deadline or minimum lease; command-output references do not expire within a generation.
- Provider provisioning, attachment-token issuance, and product-user policy stay outside EIP methods.
