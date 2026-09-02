# Agent UI Specifications

## Overview

Agent UI is the local single-user workstation distributed as `a13n-ui`. It embeds the Harness in one process-local `AgentUiApp`, reads human-editable configuration resources, discovers trusted extensions and Capabilities, groups local roots into Projects, and runs continuation-backed root and child Threads through the same CLI and WebUI application boundary.

Agent UI provides best-effort local continuation rather than durable workflow execution. It stores complete Harness checkpoints at explicit boundaries, but it does not durably accept root input, recover active operating-system processes, lease work across workers, or provide distributed failover. Foundation Service remains the durable hosted product.

Agent UI depends on the [Harness](../agent-harness/README.md), [Environment Provider package](../agent-environment-provider/README.md), and [Agent Stream Protocol](../agent-stream-protocol/README.md) through their public contracts. It does not reproduce their Agent loop, Environment operation, or observation semantics.

## Document Catalog

| Document                                                                                         | Owning contract                                                                                                                |
| ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| [00-overview.md](00-overview.md)                                                                 | Product boundary, architecture, end-to-end flows, completion boundaries, and packaging                                         |
| [01-configuration-and-resource-catalog.md](01-configuration-and-resource-catalog.md)             | Multi-file configuration source, resource identity, accepted generations, defaults, file mutation, credentials, and validation |
| [01a-extension-discovery-and-management.md](01a-extension-discovery-and-management.md)           | Capability discovery and the Harness Plugin, Environment Provider, and Environment Run Extension planes                        |
| [02-agent-composition-and-snapshots.md](02-agent-composition-and-snapshots.md)                   | Agent, MCP, Markdown subagent, import, graph resolution, tool configuration, and per-Run composition                           |
| [02a-model-authentication-and-account-stores.md](02a-model-authentication-and-account-stores.md) | API-key and subscription Model authentication, Codex/Grok compatible account stores, login reuse, and refresh boundaries       |
| [03-local-storage-and-recovery.md](03-local-storage-and-recovery.md)                             | Thread configuration heads, immutable Run/checkpoint values, Environment state, and local recovery                             |
| [04-projects-threads-and-environments.md](04-projects-threads-and-environments.md)               | Project roots, sticky Thread configuration, Run-time overrides, Environment binding, and state publication                     |
| [05-runtime-subagents-and-surfaces.md](05-runtime-subagents-and-surfaces.md)                     | `AgentUiApp`, root Runs, async child execution, Thread tools, CLI, WebUI, and live presentation                                |

## Reading Paths

### Understand the Product

Read `00`, `03`, `04`, and `05`. Then read the Harness and Agent Stream Protocol catalogs for the embedded execution and presentation contracts.

### Configure Agents, Extensions, and Subagents

Read `01`, `01a`, `02`, and `02a`, then [Harness Capability Model](../agent-harness/04-capability-model.md), [Harness Plugin System](../agent-harness/05-plugin-system.md), and [Delegation and Subagents](../agent-harness/11-delegation-and-subagents.md).

### Integrate Environments

Read `01a` and `04`, then [Provider Specifications and Catalog](../agent-environment-provider/01-provider-specs-and-catalog.md) and [Environment Re-entry Lifecycle](../agent-environment-provider/02-resource-management-and-attachments.md).

### Implement a Surface

Read `05`. A surface calls `AgentUiApp` commands and queries and consumes detached projections and live events. It does not read SQLite, interpret Harness-private events, construct Providers, or own another Thread model.

## Authority Rules

01. The selected `a13n-ui.yaml` and its fixed sibling resource directories are the only desired-resource authority. SQLite retains accepted-generation indexes and mutable Thread/runtime heads but does not become a second editable definition source.
02. A successful stable read accepts one coherent configuration generation. Invalid or partially saved files leave the previous generation active.
03. Projects, Models, configured extensions, MCP servers, Agents, and Markdown subagents have stable file-defined IDs. Global defaults select them only when a root Thread is created. Model credentials remain external references or compatible product account-store state.
04. Every Thread owns a mutable, versioned, sticky configuration. Omitted changes retain its previous selection; an admitted Run captures one immutable resolved composition that later file or Thread changes cannot alter.
05. `AgentUiApp` owns configuration mutation preconditions, Project and Thread orchestration, Host-authoritative Environment state, root admission, async child execution, and live presentation.
06. Harness and Pydantic AI own native Agent construction, Agent loops, public stream items, results, Capability behavior, and `HarnessState` continuation semantics.
07. The Environment Provider package owns Provider configuration, fresh adapter construction, `EnvironmentState` codecs, and non-destructive `close()`. Agent UI owns Project-root binding, runtime collaborators, current state, and changed-only publication.
08. Every independent root or async child Run receives fresh Model, Harness Plugin, MCP, Provider-runtime, Environment-adapter, and Environment Run Extension collaborators. Run-owned shell processes never survive their Harness Run.
09. Root and child continuation checkpoints are independent authorities. Compact AG-UI child display is inspection history and never reconstructs `HarnessState`.
10. A saved child `running` status is only a nonterminal persistence fact. Agent UI does not infer process liveness or silently replay it.
11. CLI and WebUI are peers over one `AgentUiApp`. File writes from either surface require an expected source digest and never knowingly overwrite a different observed revision.
12. Multiple local processes can open one data root through ordinary SQLite and immutable-file behavior. Mutable SQLite heads use expected-version or expected-reference compare-and-select without process lock files, PID inspection, heartbeats, leases, fencing, or distributed scheduling.

## Conventions

- Python-like schemas are conceptual unless explicitly described as serialized configuration.
- A Thread is one continuation-backed conversation identity. A root Thread has no parent; an async child Thread records its parent and can contain several linked execution segments.
- A Project is a mutable named ordered list of local roots. There is no separate Workspace resource or `WorkspaceBinding` domain model.
- A Thread configuration is a sticky selection of Project, Agent, Environment profile, Harness Plugins, Environment Run Extensions, and MCP servers. It is not a Harness Run or immutable history.
- A Run composition is the immutable resolved value captured at admission from one configuration generation and one Thread configuration version.
- An Environment profile selects Provider and Host-adapter configuration for Project-root execution. It is distinct from a runtime `Environment` identity and does not own the roots.
- Presentation values are projections. Only selected `HarnessState` checkpoints authorize continuation.
