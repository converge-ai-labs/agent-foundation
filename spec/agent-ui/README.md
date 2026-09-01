# Agent UI Specifications

## Overview

This directory defines `agent-ui`, the complete local single-user Agent workstation distributed as `a13n-ui`. It owns reloadable YAML/JSON configuration, explicit local Skill sources and managed Skill packages, reusable Model, Prompt, Plugin, Agent, and Environment definitions, immutable composition snapshots, continuation-backed Sessions, Host-owned Environment state and backing-target lifecycle, Runner-local async-subagent execution, one stable `AgentUiHost`, replaceable runtime Runners, a normal terminal CLI, and a complete multi-Session WebUI.

Agent UI uses best-effort continuation persistence rather than durable workflow execution. At each complete or suspended Harness result, it attempts to store and select one complete continuation. A later Run starts from the latest successfully selected continuation. Input, active Runs, partial output, live AG-UI values, and Runner-local async-child work remain process-local and can be lost when their canonical owner exits. Foundation Service remains the product for durable distributed execution.

Agent UI is not a reduced Foundation Service. The [Harness](../agent-harness/README.md) remains the process-local Agent runtime, the [Environment Provider package](../agent-environment-provider/README.md) remains the Provider, fresh adapter, and state-codec boundary, and [Agent Stream Protocol](../agent-stream-protocol/README.md) remains the Harness-to-AG-UI observation boundary. The user-facing Local Sandbox selects the distinct `a13n.local-envd` provider over an exact Agent UI-resolved envd executable; it never silently becomes Direct Local.

## Document Catalog

| Document                                                                             | Owning contract                                                                                                                            |
| ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| [00-overview.md](00-overview.md)                                                     | Local workstation architecture, best-effort continuation persistence, stable Host boundary, surfaces, packaging, and completion boundaries |
| [01-configuration-and-resource-catalog.md](01-configuration-and-resource-catalog.md) | Reloadable configuration, local Skill discovery/import, resource catalogs, credentials, validation, and accepted generations               |
| [02-agent-composition-and-snapshots.md](02-agent-composition-and-snapshots.md)       | Model, Prompt, Plugin, Skill exposure, Capability, and exact child-Agent composition; snapshots and Harness reconstruction                 |
| [03-local-storage-and-recovery.md](03-local-storage-and-recovery.md)                 | Minimal SQLite and immutable-object persistence, continuation publication and selection, multi-Host concurrency, and explicit cleanup      |
| [04-sessions-environments-and-state.md](04-sessions-environments-and-state.md)       | Session identity, Environment assignment, continuation and suspended-request continuation, fork, history projection, and cleanup           |
| [05-runtime-subagents-and-surfaces.md](05-runtime-subagents-and-surfaces.md)         | Stable Host, Runner rotation, root coordination, async subagents, provider runtimes, WebUI, CLI, and live presentation                     |

## Reading Paths

### Understand the Local Workstation

Read `00`, `03`, and `04`. Then read the [Harness catalog](../agent-harness/README.md) and [Environment Provider catalog](../agent-environment-provider/README.md) for the embedded runtime contracts.

### Configure and Compose Agents

Read `01` and `02`, then [Harness Agent Definition and Build](../agent-harness/03-agent-definition-and-build.md), [Harness Plugin System](../agent-harness/05-plugin-system.md), and [Delegation and Subagents](../agent-harness/11-delegation-and-subagents.md).

### Integrate Environment Providers

Read `01` and `04`, then [Provider Specifications and Catalog](../agent-environment-provider/01-provider-specs-and-catalog.md) and [Environment Re-entry Lifecycle](../agent-environment-provider/02-resource-management-and-attachments.md).

### Implement a Presentation Surface

Read `05`, then the [Agent Stream Protocol specification](../agent-stream-protocol/README.md). A surface consumes `AgentUiHost` operations, continuation-derived Session history, and the current process's live AG-UI stream. It does not interpret Harness events, control runtime Runners, or read storage independently.

## Authority Rules

- Human- and agent-editable configuration files own desired Model, Prompt, Plugin instance, local Skill source/package, Agent, and Environment definitions in the latest accepted configuration generation. SQLite indexes those definitions but does not replace their file authority.
- Agent UI resolves configuration into immutable content-addressed snapshots. A Session pins exact Agent and Environment snapshots; dynamic reload never mutates a running executable or an existing Session composition.
- SQLite owns small mutable indexes, Session metadata, the latest continuation reference, and Host-private Environment state associations. Immutable files own managed Skill packages, snapshots, complete Session continuation bundles, and Environment state blobs.
- `HarnessState` is the canonical continuation value. One `StoredSessionContinuation` bundles the complete state, optional exact `DeferredToolRequests`, Harness release, and creation time. AG-UI values, rendered history, browser caches, and partial model or tool output cannot replace a continuation.
- Input and execution are process-local until a complete or suspended continuation is selected. Agent UI has no durable input queue, Run ledger, event journal, projection watermark, child-job database, or delivery ledger.
- The Harness and Pydantic AI own native Agent construction, Agent loops, built child collections, run events, results, and continuation semantics. Each Agent UI Runner generation supplies one Host `SubagentOperator` to root definitions that select async mode; nested subagents remain inline. Background shell uses the Harness default Run-owned controller and no Agent UI process operator.
- The selected Runner resolves every logical Model ID from the exact pinned Agent snapshot through the installed Agent UI adapter catalog. It obtains current credential material from the configured local backend at Run time and supplies one fresh callable Harness resolver. Models, credentials, clients, and resolvers are never persisted or sent to the Host.
- The Environment Provider package owns Provider configuration, inert adapter construction, provider-owned `EnvironmentState` codecs, single-Environment operations, and explicit lifecycle methods. Agent UI owns desired mounts, current authoritative state, root/child association, changed-only publication, cleanup, and prune. The Runner constructs fresh adapters from Host-selected state and returns detached state observations for publication.
- Every independent root or async child Run receives fresh Environment adapters and one Run-local Harness mount set. Inline children borrow the parent facade. Foreground and background shell use the exact entered Environment; Run closure kills and releases default processes before adapter close. Persisted references never restore adapters, credentials, native provider objects, Run-local process handles, or authority.
- Agent Stream Protocol owns reusable Harness-to-AG-UI conversion and process-local accumulation. Agent UI owns a best-effort live fan-out hub and reconstructs retained Session history from the selected Harness continuation; it does not persist AG-UI as recovery state.
- OpenTelemetry is exported through the repository observability boundary and is not stored in Agent UI SQLite databases or Session continuation objects.
- WebUI and CLI are product peers over one `AgentUiHost`. Neither owns a separate configuration model, Session model, orchestration loop, runtime-generation controller, or rendering truth.
- Each frontend invocation owns one stable Host process. Multiple local processes can open the data root using ordinary SQLite and filesystem behavior. Updates are last-write-wins; Agent UI adds no lease, fencing, stale-writer protocol, or distributed coordination. Replaceable runtime Runners own only process-local execution behavior and no continuation-selection authority.

## Specification Conventions

- Python-like schemas are conceptual unless explicitly identified as serialized local documents.
- A configuration generation is one atomically accepted view of all reloadable configuration sources; it is not a process generation or Session revision.
- A resource revision is immutable normalized content identified by stable resource identity plus digest; a source file can later select different content without mutating the revision.
- A Session is one local continuation-backed interaction history and is not a Foundation `Execution`, browser connection, Pydantic run, model-provider session, or Environment backing target.
- A Harness Run is process-local. Starting another Run from a continuation is continuation, not recovery of the old Run.
- The root Thread identity is carried by `HarnessState.thread_id`; Agent UI does not persist an independent local Thread/Run lifecycle model.
- Presentation items are projections of selected continuation history plus current live output. They are not continuation authority.
- Async-subagent executions are Runner-local Host-operator work. Records disappear with that Runner, and a generation-aware child-completion event can wake an inactive Session from its latest selected continuation. Background processes remain inside one Harness Run, expose only active-Run readiness and polling, and have no Agent UI record or post-Run wake.
