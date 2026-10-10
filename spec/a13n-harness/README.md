# Harness Specifications

## Overview

This directory defines `a13n-harness`, the Pydantic AI-based process-local execution library used by embedded applications and hosted workers.

The Harness owns code-first Agent construction, a neutral package-local official model catalog, SDK-first process-local Model OAuth, default-on build-time model-cost valuation, a narrow optional plugin configuration/loading boundary, trusted outer plugins, fresh typed Run context, Run-local Environment entry and multi-mount routing, optional Run-scoped model resolution, bounded model-interruption recovery, native Pydantic execution, portable continuation state, normalized events, results, and cleanup. It does not own durable Agent schemas, Presets, artifact installation or trust, execution records, queues, worker leases, delivery, or billing.

## Document Catalog

| Document                                                                                 | Owning contract                                                                                                                  |
| ---------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md)                                                         | Architecture, recovery layering, completion boundaries, and principles                                                           |
| [01-pydantic-ai-foundation.md](01-pydantic-ai-foundation.md)                             | Native Pydantic AI primitive mapping and compatibility                                                                           |
| [02-domain-model.md](02-domain-model.md)                                                 | Process-local identities, entities, and version boundaries                                                                       |
| [03-agent-definition-and-build.md](03-agent-definition-and-build.md)                     | Code-first `AgentDefinition`, official model facts, builder, executable ownership, and Host reconstruction                       |
| [04-capability-model.md](04-capability-model.md)                                         | Native Capability composition, `AgentContext`, and namespaced state                                                              |
| [05-plugin-system.md](05-plugin-system.md)                                               | Plugin document/Build Context, selected factories, concrete middleware, ordering, binding, result/state composition, and cleanup |
| [06-execution-context-and-lifecycle.md](06-execution-context-and-lifecycle.md)           | Logical run lifecycle, inner model attempts, cancellation, terminal results, and cleanup                                         |
| [07-tool-execution.md](07-tool-execution.md)                                             | Native and managed function tools, client-side external tools, policy, credentials, and deferred results                         |
| [08-environment-integration.md](08-environment-integration.md)                           | Environment sources, first-use readiness, routes, Run Extensions, mutation, model projection, state, and cleanup                 |
| [Environment library](../a13n-environment/README.md)                                     | Independent single-target management, Environment connectors and Environment executions, portable state, and Providers           |
| [09-context-and-memory.md](09-context-and-memory.md)                                     | History, runtime context, handoff, skills, working state, resource acquisition, and compaction                                   |
| [10-snapshot-and-resume.md](10-snapshot-and-resume.md)                                   | `HarnessState`, interrupted-history normalization, import/export, and Host persistence boundary                                  |
| [11-delegation-and-subagents.md](11-delegation-and-subagents.md)                         | Child topology, Harness-private inline execution, standard async Toolsets, and the Host operator boundary                        |
| [12-events-observability-and-usage.md](12-events-observability-and-usage.md)             | Process-local events, mixed-source usage attribution, pricing catalogs, reporting, and accounting boundary                       |
| [13-hosting-contract.md](13-hosting-contract.md)                                         | Host-owned schemas/reconstruction, fresh bindings, durable lifecycle, and completion mapping                                     |
| [14-public-api-and-packaging.md](14-public-api-and-packaging.md)                         | Public Python API, package boundary, errors, and compatibility                                                                   |
| [15-security-compatibility-and-tradeoffs.md](15-security-compatibility-and-tradeoffs.md) | Trust boundaries, authority, data safety, compatibility, and trade-offs                                                          |
| [16-input-model-and-output.md](16-input-model-and-output.md)                             | Native input, thin model resolution, self-healing, semantic recovery, streaming, and output                                      |
| [16a-model-authentication.md](16a-model-authentication.md)                               | SDK-first Model OAuth credentials, Host sources, refresh lifecycle, request isolation, and provider compatibility                |
| [16b-model-provider-definitions.md](16b-model-provider-definitions.md)                   | Typed native Model construction, calling-API bindings, and bounded connection probes                                             |
| [17-core-capability-catalog.md](17-core-capability-catalog.md)                           | Documentation catalog for mandatory and optional Capability composition roles                                                    |
| [18-codeact.md](18-codeact.md)                                                           | Restricted inline and file-backed CodeAct orchestration, typed tool eligibility, sandbox lifecycle, and nested dispatch          |
| [19-observation-model.md](19-observation-model.md)                                       | OpenTelemetry signals, trace levels, metrics, correlation, information boundary, Host profiles, and export-failure semantics     |
| [20-async-components-and-lifecycle.md](20-async-components-and-lifecycle.md)             | Async subagent admission, observation, parent closure, wake, restart, loss, retention, and Host shutdown                         |
| [21-file-memory.md](21-file-memory.md)                                                   | File memory store contract, file format, mounts, tools, context cursors, and the local directory store                           |
| [21a-record-memory.md](21a-record-memory.md)                                             | Record memory store contract, Memory Providers and mem0 over REST, mounts, tools, and recall                                     |
| [22-provider-subsystem.md](22-provider-subsystem.md)                                     | Shared Provider core across all five domains, credential declaration, catalogs, and the installed Environment manifest           |

## Reading Paths

### Understand the Harness

Read `00`, `01`, and `02`, then follow the owner for the concern being changed.

### Build an Agent or Plugin

Read `03`, `04`, `05`, and `14`. Hosted durable definitions are owned by [Service resources](../a13n-service/04-resources.md).

### Author or Select a Provider

Read `22` for the shared Provider core, credential declaration, catalogs, and the installed Environment manifest, then the owning domain: `16b` for Model, the [Environment library](../a13n-environment/README.md) for Environment, `21a` for Memory, and [Service providers](../a13n-service/08-providers.md) for the Connector and Web deployment boundaries.

### Understand Models and Recovery

Read `06`, `10`, and `16`. Read `16a` for OAuth-backed native Models and Host credential sources. Provider transport retry, exact history repair, `ModelAttempt` recovery, and durable Host recovery have separate owners.

### Integrate Tools or Environments

Read `07`, `08`, `13`, and `15`, then the [Environment library](../a13n-environment/README.md) for management, connection, and provider guarantees, and `22` for the shared Provider core. For restricted Python orchestration over tools, also read `18`. Read `11` and `20` for async subagents; `08` solely owns background-process semantics. A Run calls its Host sources' `ensure_ready()` on first use, opens independent executions from the returned connectors, and exposes one internal multi-mount facade; `DynamicEnvironmentCapability` is only the optional model-facing projection. For durable client-tool delivery, also read [Service runs](../a13n-service/05-runs.md).

### Add Agent Memory

Read `21` for file memory and `21a` for record memory, then `09` for the model context projection they use and `07` for tool permissions over `memory.file.*` and `memory.record.*`.

### Implement Hosting or Persistence

Read `10`, `12`, `13`, `14`, and `20`, then the [a13n Service specification](../a13n-service/README.md). A Host owns all async-child lifecycle authority. Shell references and observations are Run-local; cleanup releases them without blanket termination. Provider state owns native process recovery. See `08` for the independent process and output completion boundaries.

### Integrate Observation

Read `06`, `19`, `13`, and `15`. Read `12` separately for process-local events and usage; telemetry never replaces either contract.

## Authority Rules

- Pydantic AI owns the Agent loop and its native Model, Capability, Toolset, message, deferred, output, event, and usage contracts.
- The shared [interaction model](../interaction-model.md) owns Session, Thread, Run, and Item meaning; `HarnessState` carries the stable identity and continuation of one Thread.
- Concrete plugins and other native Python inputs are trusted in-process objects; the narrow Harness plugin document is an optional builder-local source, not an Agent definition format.
- The Harness owns process-local code-first construction and logical-Run behavior.
- A Host owns durable definition schemas, Presets, revisions, artifact locks, reconstruction adapters, Session/Run lifecycle, execution lifecycle, and delivery.
- Provider definitions are inert immutable values; a definition's runtime factory is the only place it acquires a live collaborator.
- Environment definitions and Environment connectors are inert; the shared library implements explicit management and opening an Environment execution, while Hosts own state and lifecycle policy.
- A telemetry backend observes execution but never becomes lifecycle authority.

## Specification Conventions

- Python-like schemas are conceptual typed contracts unless explicitly declared as wire formats.
- A process-local Python object is not durable merely because a Host can reconstruct it.
- One durable fact has one owner; cross-documents link rather than duplicate schemas.
- Identifiers provide correlation and never grant authority by themselves; `thread_id`, `run_id`, and model-attempt IDs remain distinct.
- Recovery text must preserve unknown side effects and must not claim exactly-once behavior without provider evidence.
