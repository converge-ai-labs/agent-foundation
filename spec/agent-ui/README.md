# Agent UI Specifications

## Overview

Agent UI is the local single-user workstation distributed as `a13n-ui`. It embeds the Harness in one process-local `AgentUiApp`, reads human-editable configuration resources, discovers trusted extensions and Capabilities, groups local roots and root Threads into Projects, and runs continuation-backed root and child Threads through the same CLI, TUI, and WebUI application boundary.

Agent UI provides best-effort local continuation rather than durable workflow execution. It stores complete Harness checkpoints at explicit boundaries, but it does not durably accept root receipts, input, or deferred responses, recover active operating-system processes, lease work across workers, or provide distributed failover. Foundation Service remains the durable hosted product.

Agent UI depends on the [Harness](../agent-harness/README.md), [Environment Provider package](../agent-environment-provider/README.md), and [Agent Stream Protocol](../agent-stream-protocol/README.md) through their public contracts. It does not reproduce their Agent loop, Environment operation, or observation semantics.

## Document Catalog

| Document                                                                                         | Owning contract                                                                                                                |
| ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| [00-overview.md](00-overview.md)                                                                 | Product boundary, architecture, end-to-end flows, completion boundaries, and packaging                                         |
| [01-configuration-and-resource-catalog.md](01-configuration-and-resource-catalog.md)             | Multi-file configuration source, resource identity, accepted generations, defaults, file mutation, credentials, and validation |
| [01a-extension-discovery-and-management.md](01a-extension-discovery-and-management.md)           | Capability discovery and the Harness Plugin, Environment Provider, and Environment Run Extension planes                        |
| [02-agent-composition-and-snapshots.md](02-agent-composition-and-snapshots.md)                   | Agent, MCP, Markdown subagent, import, graph resolution, tool configuration, and per-Run composition                           |
| [02a-model-authentication-and-account-stores.md](02a-model-authentication-and-account-stores.md) | API-key and subscription Model authentication, Codex/Grok compatible account stores, native login, and refresh boundaries      |
| [02b-environment-skill-sources.md](02b-environment-skill-sources.md)                             | Host-path-preserving and virtual multi-mount Skill sources, user Skill mount, precedence, and per-Run freezing                 |
| [03-local-storage-and-recovery.md](03-local-storage-and-recovery.md)                             | Thread metadata/configuration heads, immutable Run/checkpoint values, Environment state, and local recovery                    |
| [04-projects-threads-and-environments.md](04-projects-threads-and-environments.md)               | Project roots, Full Control and Sandbox modes, path layouts, Thread configuration, Environment binding, and state publication  |
| [05-runtime-subagents-and-surfaces.md](05-runtime-subagents-and-surfaces.md)                     | `AgentUiApp`, detached projections, root operations, async children, Web listener access, tools, and live presentation         |
| [tui/](tui/README.md)                                                                            | Textual terminal workstation, Focus and Workbench interaction, semantic presentation, and terminal runtime                     |
| [webui/](webui/README.md)                                                                        | React browser workstation, client data and live state, Thread interaction, configuration management, and design system         |

## Reading Paths

### Understand the Product

Read `00`, `03`, `04`, and `05`. Then read the Harness and Agent Stream Protocol catalogs for the embedded execution and presentation contracts. Read [TUI Specifications](tui/README.md) for the terminal-native workstation and [WebUI Specifications](webui/README.md) for the browser workstation.

### Configure Agents, Extensions, and Subagents

Read `01`, `01a`, `02`, `02a`, and `02b`, then [Harness Capability Model](../agent-harness/04-capability-model.md), [Harness Plugin System](../agent-harness/05-plugin-system.md), [Context and Skills](../agent-harness/09-context-and-memory.md), and [Delegation and Subagents](../agent-harness/11-delegation-and-subagents.md).

### Integrate Environments

Read `01a`, `02b`, and `04`, then [Provider Specifications and Catalog](../agent-environment-provider/01-provider-specs-and-catalog.md) and [Environment Re-entry Lifecycle](../agent-environment-provider/02-resource-management-and-attachments.md).

### Implement a Surface

Read `05`. A surface calls `AgentUiApp` commands and queries and consumes detached projections and live events. It does not read SQLite, interpret Harness-private events, construct Providers, or own another Thread model. A terminal implementation also follows the [TUI Specifications](tui/README.md); the bundled browser implementation follows the [WebUI Specifications](webui/README.md).

## Authority Rules

01. The selected `a13n-ui.yaml` and its fixed sibling resource directories are the only desired-resource authority. SQLite retains accepted-generation indexes and mutable Thread/runtime heads but does not become a second editable definition source.
02. A successful stable read accepts one coherent configuration generation. Invalid or partially saved files leave the previous generation active.
03. Projects, Models, configured extensions, MCP servers, Agents, and Markdown subagents have stable file-defined IDs. Global defaults select them only when a root Thread is created. Model credentials remain external references or compatible product account-store state.
04. Every Thread owns independent mutable metadata and sticky-configuration heads. Omitted configuration changes retain the previous selection; an admitted Run captures one immutable resolved composition that later file, Project, or Thread changes cannot alter.
05. `AgentUiApp` owns configuration mutation preconditions, detached Project and Thread projections, Host-authoritative Environment state, process-local root receipts and deferred response, async child execution, and live presentation.
06. Harness and Pydantic AI own native Agent construction, Agent loops, public stream items, results, Capability behavior, and `HarnessState` continuation semantics.
07. The Environment Provider package owns Provider configuration, fresh adapter construction, `EnvironmentState` codecs, and non-destructive `close()`. Agent UI owns Project-root binding, runtime collaborators, current state, and changed-only publication.
08. Every independent root or async child Run receives fresh Model, Harness Plugin, MCP, Provider-runtime, Environment-adapter, and Environment Run Extension collaborators. Run-owned shell processes never survive their Harness Run.
09. Root and child continuation checkpoints are independent authorities. Compact AG-UI child display is inspection history and never reconstructs `HarnessState`.
10. Saved root and child facts never imply current-process liveness. Root receipts and all control availability are process-local; Agent UI does not infer liveness or silently replay work.
11. CLI, TUI, and WebUI are peers over one `AgentUiApp` and receive detached bounded surface values. The CLI reads desired configuration and exposes explicit imports but no generic resource mutation; the WebUI manages Project and other desired-resource files through expected source digests; the TUI derives a launch Project filter from the current directory, inspects accepted resources and catalogs, and patches supported non-Project sticky selections.
12. Focused live delivery follows complete root lineage and uses an epoch/sequence snapshot cutover. App-wide summary invalidations are best-effort refetch hints, not durable truth.
13. Multiple local processes can open one data root through ordinary SQLite and immutable-file behavior. Mutable SQLite heads use expected-version or expected-reference compare-and-select without process lock files, PID inspection, heartbeats, leases, fencing, or distributed scheduling.

## Conventions

- Python-like schemas are conceptual unless explicitly described as serialized configuration.
- A Thread is one continuation-backed conversation identity. A root Thread has no parent; an async child Thread records its parent and can contain several linked execution segments.
- A Project is a file-defined mutable named ordered list of local roots and the only root-Thread organization used by Agent UI. Its first root anchors current-directory launch resolution and receives mount alias `workspace`; later roots are additional Run mounts with distinct aliases. Agent UI defines no Workspace resource.
- A Thread configuration is a sticky selection of Project, Agent, Environment profile, Harness Plugins, Environment Run Extensions, and MCP servers. It is not a Harness Run or immutable history.
- A Run composition is the immutable resolved value captured at admission from one configuration generation and one Thread configuration version.
- An Environment profile selects Provider and Host-adapter configuration for Project-root execution. Agent UI owns the fixed Full Control and Sandbox profiles; extension YAML can define advanced custom profiles under other IDs. A profile is distinct from a runtime `Environment` identity and does not own the roots.
- Presentation values are strict detached projections. Only selected `HarnessState` checkpoints authorize continuation.
- A root operation is one process-local prompt or deferred-response admission identified by an exact receipt. It is not a durable Run record.
