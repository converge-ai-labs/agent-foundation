# Agent UI Specifications

## Overview

Agent UI is the local single-user workstation distributed as `a13n-ui`. It embeds the Harness in one process-local `AgentUiApp`, composes a small Agent UI-owned configuration into exact Harness definitions, runs continuation-backed Sessions, binds local folders when a message is submitted, manages Host-authoritative Environment state, persists inspectable async child Threads, and exposes the same application operations through CLI and WebUI adapters.

Agent UI provides best-effort local continuation rather than durable workflow execution. It stores complete root and child Harness checkpoints at explicit boundaries, but it does not durably accept root input, recover active operating-system processes, lease work across workers, or provide distributed failover. Foundation Service remains the durable hosted product.

Agent UI depends on the [Harness](../agent-harness/README.md), [Environment Provider package](../agent-environment-provider/README.md), and [Agent Stream Protocol](../agent-stream-protocol/README.md) through their public contracts. It does not reproduce their Agent loop, Environment operation, or observation semantics.

## Document Catalog

| Document                                                                             | Owning contract                                                                                                  |
| ------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md)                                                     | Product boundary, architecture, end-to-end flows, completion boundaries, and packaging                           |
| [01-configuration-and-resource-catalog.md](01-configuration-and-resource-catalog.md) | The strict `a13n-ui.yaml`, trusted catalogs, reload, credentials, and validation                                 |
| [02-agent-composition-and-snapshots.md](02-agent-composition-and-snapshots.md)       | Agent composition, canonical Markdown subagents, cross-tool migration, graph resolution, and immutable snapshots |
| [03-local-storage-and-recovery.md](03-local-storage-and-recovery.md)                 | Session and child-Thread persistence, immutable checkpoints, and local recovery                                  |
| [04-sessions-environments-and-state.md](04-sessions-environments-and-state.md)       | Session identity, message-time `WorkspaceBinding`, Environment profiles, binding, and state publication          |
| [05-runtime-subagents-and-surfaces.md](05-runtime-subagents-and-surfaces.md)         | `AgentUiApp`, root Runs, persisted async child execution, Session tools, CLI, WebUI, and live presentation       |

## Reading Paths

### Understand the Product

Read `00`, `03`, and `05`. Then read the Harness and Agent Stream Protocol catalogs for the embedded execution and presentation contracts.

### Configure Agents and Subagents

Read `01` and `02`, then [Harness Agent Definition and Build](../agent-harness/03-agent-definition-and-build.md), [Harness Plugin System](../agent-harness/05-plugin-system.md), and [Delegation and Subagents](../agent-harness/11-delegation-and-subagents.md).

### Integrate Environments

Read `04`, then [Provider Specifications and Catalog](../agent-environment-provider/01-provider-specs-and-catalog.md) and [Environment Re-entry Lifecycle](../agent-environment-provider/02-resource-management-and-attachments.md).

### Implement a Surface

Read `05`. A surface calls `AgentUiApp` commands and queries and consumes detached projections and live events. It does not read SQLite, interpret Harness-private events, construct Providers, or own another Session model.

## Authority Rules

01. `a13n-ui.yaml` and its sibling canonical `subagents/*.md` files own desired Agent UI configuration. SQLite does not become a second configuration authority.
02. One accepted configuration resolves to immutable Agent and Environment-profile snapshots. A Session pins exact snapshots; reload never mutates an existing Session graph.
03. `AgentUiApp` owns application orchestration, Session and child-Thread persistence, Host-authoritative Environment state, root admission, async child execution, and live presentation.
04. Harness and Pydantic AI own native Agent construction, Agent loops, public stream items, results, and `HarnessState` continuation semantics.
05. The Environment Provider package owns Provider configuration, fresh adapter construction, `EnvironmentState` codecs, and non-destructive `close()`. Agent UI owns workspace binding, current state, and changed-only publication; destructive Provider lifecycle is outside the current Agent UI contract.
06. Every independent root or async child Run receives fresh Model, plugin, MCP, Provider-runtime, and Environment-adapter collaborators. Run-owned shell processes never survive their Harness Run.
07. Root Session continuation and child Thread checkpoints are independent authorities. Compact AG-UI child display is inspection history and never reconstructs `HarnessState`.
08. A successful async child execution is not published as `succeeded` until its terminal checkpoint is durably selected. A saved `running` status is only a nonterminal persistence fact; Agent UI does not infer process liveness or silently replay it.
09. CLI and WebUI are peers over one `AgentUiApp`. Neither surface owns storage authority, another orchestration loop, or a Runner generation.
10. Multiple local processes can open one data root through ordinary SQLite and immutable-file behavior. Mutable heads use expected-reference compare-and-select, so a concurrent change fails explicitly instead of overwriting retained truth. Agent UI adds no process lock files, PID inspection, heartbeats, leases, fencing, distributed scheduling, or exactly-once effects.

## Conventions

- Python-like schemas are conceptual unless explicitly described as serialized configuration.
- A Session is one local continuation-backed root Thread. It is not a browser connection, Harness Run, Foundation Run, or Environment target.
- An async child Thread can contain several linked execution segments. Each `delegate` or `resume_subagent` creates one segment. A segment normally contains one Harness Run; an internally denied deferred request can add a bounded continuation Run with a fresh `run_id` inside the same segment.
- A `WorkspaceBinding` is an ordered tuple of local folders captured for one submitted message. It has no independent ID or persistence lifecycle.
- An Environment profile defines how a local folder is executed. It does not contain a workspace path.
- Presentation values are projections. Only selected root or child `HarnessState` checkpoints authorize continuation.
