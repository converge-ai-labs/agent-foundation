# Harness UI Specifications

## Overview

Harness UI (`a13n-harness-ui`) is a local Agent workbench with a personal CLI and a trusted-team collaborative WebUI, distributed by the independent `a13n-harness-ui` library. It embeds Harness in one reusable `HarnessUiApp`, reads human-editable resources, discovers trusted extensions and Capabilities, and retains internal Project/Thread identities for durable continuation and execution. Its native full-terminal renderer owns a bounded semantic display cache; the App remains conversation authority.

`a13n-harness-ui` starts the interactive CLI. `a13n-harness-ui webui` explicitly starts one foreground server and WebUI-mode App. The CLI and browser adapter consume the same commands, projections, receipts, and live subscriptions. The browser provides project-organized conversations, page presence, shared prompt editing, comments on saved AI output, resource configuration, and explicitly enabled native Host files, Git views, and PTY. All browser participants share one instance authority without multi-tenancy. There is no detached daemon or IPC mode.

Harness UI provides best-effort local continuation rather than durable workflow execution. It stores complete Harness checkpoints at explicit boundaries, but it does not durably accept root receipts, input, or deferred responses, recover active operating-system processes, lease work across workers, or provide distributed failover. A finalized [graceful restart handoff](03-local-storage-and-recovery.md#graceful-restart-handoff) permits single-use continuation after a clean sequential WebUI update, not crash recovery. a13n Service remains the durable hosted product.

Harness UI depends on the [Harness](../a13n-harness/README.md), [Environment package](../a13n-environment/README.md), and [Agent Stream Protocol](../a13n-stream-protocol/README.md) through their public contracts. It does not reproduce their Agent loop, Environment operation, or observation semantics.

## Document Catalog

| Document                                                                                         | Owning contract                                                                                                                     |
| ------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md)                                                                 | Product boundary, architecture, end-to-end flows, completion boundaries, and packaging                                              |
| [01-configuration-and-resource-catalog.md](01-configuration-and-resource-catalog.md)             | Multi-file configuration source, resource identity, accepted generations, defaults, file mutation, credentials, and validation      |
| [01a-extension-discovery-and-management.md](01a-extension-discovery-and-management.md)           | Capability discovery and the Harness Plugin, Environment Provider, and Environment Run Extension planes                             |
| [01b-content-plugin-repositories.md](01b-content-plugin-repositories.md)                         | Git-distributed declarative Content Plugins, CLI management, editable installation, Skills, and Markdown subagents                  |
| [02-agent-composition-and-snapshots.md](02-agent-composition-and-snapshots.md)                   | Agent, MCP, Markdown subagent, import, graph resolution, tool configuration, and per-Run composition                                |
| [02a-model-authentication-and-account-stores.md](02a-model-authentication-and-account-stores.md) | API-key and subscription Model authentication, Codex/Grok compatible account stores, native login, and refresh boundaries           |
| [02b-environment-skill-sources.md](02b-environment-skill-sources.md)                             | Host-path-preserving and virtual multi-mount Skill sources, user Skill mount, precedence, and per-Run freezing                      |
| [03-local-storage-and-recovery.md](03-local-storage-and-recovery.md)                             | Thread metadata/configuration heads, immutable Run/checkpoint values, Environment state, output-comment storage, and local recovery |
| [04-projects-threads-and-environments.md](04-projects-threads-and-environments.md)               | Project roots, Full Control and Sandbox modes, path layouts, Thread configuration, Environment binding, and state publication       |
| [04a-devices-and-environment-bindings.md](04a-devices-and-environment-bindings.md)               | Device connections, directory discovery, binding selection, admission capture and WebUI Add environment                             |
| [05-runtime-subagents-and-surfaces.md](05-runtime-subagents-and-surfaces.md)                     | `HarnessUiApp`, detached projections, root operations, async children, Web listener access, tools, and live presentation            |
| [06-setup-and-environment-readiness.md](06-setup-and-environment-readiness.md)                   | First-use discovery, reviewed starter files, explicit defaults, and selected Environment preflight/recovery                         |
| [07-interactive-cli.md](07-interactive-cli.md)                                                   | Full-terminal ownership, commands, display modes, startup, cwd sessions, and context choices                                        |

The [WebUI catalog](webui/README.md) indexes the browser workbench, page presence and collaborative conversations, saved-output comments, native computer sharing, and bundled/Docker distribution contracts. Listener and HTTP/API behavior remain owned by `05`; transient drafts and durable comments retain their separate storage boundaries in `03`.

## Reading Paths

### Understand the Product

Read `00`, `03`, `04`, `05`, and `06`. Then read the Harness and Agent Stream Protocol catalogs for the embedded execution and presentation contracts. Read [Interactive CLI](07-interactive-cli.md) for the terminal contract.

### Configure Agents, Extensions, and Subagents

Read `01`, `01a`, `01b`, `02`, `02a`, and `02b`, then [Harness Capability Model](../a13n-harness/04-capability-model.md), [Harness Plugin System](../a13n-harness/05-plugin-system.md), [Context and Skills](../a13n-harness/09-context-and-memory.md), and [Delegation and Subagents](../a13n-harness/11-delegation-and-subagents.md).

### Integrate Environments

Read `01a`, `02b`, `04`, and `04a`, then [Provider Specifications and Catalog](../a13n-environment/01-provider-specs-and-catalog.md) and [Environment Re-entry Lifecycle](../a13n-environment/02-environment-lifecycle.md).

### Implement a Surface

Read `05`. A surface calls `HarnessUiApp` commands and queries and consumes detached projections and live events. It does not read SQLite, interpret Harness-private events, construct Providers, or own another Thread model. The terminal follows [Interactive CLI](07-interactive-cli.md); the HTTP adapter reuses the same application authority, the [WebUI](webui/README.md) adds collaborative editing and optional native human access through that same App, without adding Environment debug files or terminals.

## Authority Rules

01. The selected `a13n-harness-ui.yaml` and its fixed sibling resource directories are the primary desired-resource authority. The data-root Content Plugin catalog contributes separately managed editable Skill and Markdown content; SQLite retains accepted-generation indexes and mutable Thread/runtime heads but does not become an editable definition source.
02. A successful stable read accepts one coherent configuration generation. Invalid or partially saved files leave the previous generation active.
03. Projects, Models, configured extensions, MCP servers, Agents, and Markdown subagents have stable file-defined IDs. Explicit choices, Project defaults, Agent-owned defaults, and global defaults initialize root Thread selections under the configuration precedence contract. Model credentials remain external references or compatible product account-store state.
04. Every Thread owns independent mutable metadata and sticky-configuration heads. Omitted configuration changes retain the previous selection; an admitted Run captures one immutable resolved composition that later file, Project, or Thread changes cannot alter.
05. `HarnessUiApp` owns validated last-write-wins configuration-file publication, detached Project and Thread projections, Host-authoritative Environment state, process-local root receipts and deferred response, async child execution, and live presentation.
06. Harness and Pydantic AI own native Agent construction, Agent loops, public stream items, results, Capability behavior, and `HarnessState` continuation semantics.
07. The Environment package owns Provider configuration, fresh adapter construction, `EnvironmentState` codecs, and non-destructive `close()`. Harness UI owns Project-root binding, runtime collaborators, current state, and changed-only publication.
08. Every independent root or async child Run receives fresh Model, Harness Plugin, MCP, Provider-runtime, Environment-adapter, and Environment Run Extension collaborators. Shell references are Run-local; native command survival and recovery follow the Provider Environment state contract.
09. Root and child continuation checkpoints are independent authorities. Compact AG-UI child display is inspection history and never reconstructs `HarnessState`.
10. Saved root and child facts never imply current-process liveness. Root receipts and all control availability are process-local; Harness UI does not infer liveness or silently replay work. Graceful-restart continuation requires a separately committed, single-use handoff.
11. The full-terminal CLI is an adapter over one reusable `HarnessUiApp`. Desired configuration remains editable; setup publication is explicit. Project and Thread management are not terminal workflows, but their durable identities and existing history remain intact.
12. Focused live delivery follows complete root lineage and uses an epoch/sequence snapshot cutover. App-wide summary invalidations are best-effort refetch hints, not durable truth.
13. Multiple local processes can open one data root through ordinary SQLite and immutable-file behavior. Mutable SQLite heads use expected-version or expected-reference compare-and-select without process lock files, PID inspection, heartbeats, leases, fencing, or distributed scheduling. Independent Apps do not thereby share live collaboration or execution receipts.
14. Page/editor presence and shared browser drafts are in-memory collaboration state, not selected continuation or durable root-work acceptance. Published output comments are independently persisted human discussion and never enter model context automatically. Reconnect never authorizes automatic submission.
15. Native Host files, Git views, and PTY are explicitly enabled human operations on the server OS, independent of Agent Environment policy and Run lifetime.

## Conventions

- Python-like schemas are conceptual unless explicitly described as serialized configuration.
- A Thread is one continuation-backed conversation identity. A root Thread has no parent; an async child Thread records its parent and can contain several linked execution segments.
- A Project is optional file-defined working context with local roots and conversation creation configuration; a remote-only Project can have no local roots. Its first root anchors current-directory launch resolution and receives mount alias `workspace`; later roots are additional Run mounts with distinct aliases. Harness UI defines no Workspace resource.
- A Thread configuration is a sticky selection of optional Project, Agent, local Environment profile, additional Environment bindings/default, Harness Plugins, Environment Run Extensions and MCP servers. It is not a Harness Run or immutable history.
- A Run composition is the immutable resolved value captured at admission from one configuration generation and one Thread configuration version.
- An Environment profile selects Provider and Host-adapter configuration for Project-root execution. Harness UI owns the fixed Full Control and Sandbox profiles; extension YAML can define advanced custom profiles under other IDs. A profile is distinct from a runtime `Environment` identity and does not own the roots.
- Presentation values are strict detached projections. Only selected `HarnessState` checkpoints authorize continuation.
- A root operation is one process-local prompt or deferred-response admission identified by an exact receipt. It is not a durable Run record.
