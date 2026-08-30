# Foundation Service

## Design Position

This directory defines `foundation-service`, the optional durable Host that embeds `agent-harness`. It is a modular service with independently selectable control and worker process roles, not another Agent loop and not a collection of independently versioned microservices.

Foundation owns managed Secrets, ModelConfigs, resource authorization,
serializable AgentPreset authoring resources, immutable AgentPresetVersions,
Workspace Skill resources and immutable package revisions, trusted Harness
plugin artifacts and Runtime locks, durable Threads, editable queued
submissions, Turns, and TurnAttempts, scheduling, the durable Thread inbox, waiting pending summaries and exact
Turn-state requests, Environment connection configuration in Turn state,
lifecycle events, raw usage records, and the public management API.
The control surface also owns the Foundation Service Protocol Gateway, which
maps Native, Hosted AG-UI, and A2A callers into the same application authority.

It does not redefine the code-first Harness `AgentDefinition`, Pydantic Agent loop, Harness result and state semantics, Agent Stream Protocol conversion, Environment provider lifecycle types, EIP, or provider-native state. Platform-owned data and APIs follow [Platform Data Conventions](../data-conventions.md) and [Platform API Conventions](../api-conventions.md).

## Accepted Identity Model

The shared [Platform Interaction Model](../interaction-model.md) owns the public concepts `Session`, `Thread`, `Turn`, and `Item`. Foundation uses Turn and TurnAttempt directly as its durable scheduling identities:

```text
Session -> Thread -> Turn -> TurnAttempt -> Harness Run -> ModelAttempt
```

Every Foundation-managed Agent invocation accepts a Turn belonging to exactly one Session and Thread. Interactive requests, schedules, webhooks, service requests, and asynchronous children use the same Worker scan, TurnAttempt, and recovery contract.

Each hosted Thread is an independent versioned relational resource. It owns
Session membership, origin, the current Turn (the most recently accepted Turn),
the selected continuation head, and optimistic concurrency for accepted
advancement; Turn rows remain the durable work and state DAG. Whether the
current Turn is active derives from its status.

A non-terminal Turn can span several process-local Harness Runs when Worker takeover creates another TurnAttempt. Under [Agent Control: Input and Continuation](34-agent-control-input-and-continuation.md), a waiting Turn is sealed; authenticated feedback accepts a new Turn whose `parent_turn_id` names that waiting Turn. The new Turn receives fresh state and, when claimed, its own TurnAttempt, fresh `RunBindings`, Environment attachments, runtime mounts, `EnvironmentRuntime`, and Harness Run. One `TurnAttempt` starts at most one Harness Run; internal Harness `ModelAttempt` values are not durable worker generations. Neither `turn_attempt_id` nor `run_id` replaces `turn_id` or `thread_id`.

## Specification Catalog

| Document                                                                                              | Owning contract                                                                                                                             |
| ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| [00 Overview](00-overview.md)                                                                         | Service shape, end-to-end flow, subsystem boundaries, dependency direction, and completion boundaries                                       |
| [01 Runtime Configuration and Deployment](01-runtime-configuration-and-deployment.md)                 | Configuration precedence, deployment profiles, control and worker roles, startup, readiness, supervision, drain, and shutdown               |
| [02 Distribution Composition and Extensions](02-distribution-composition-and-extensions.md)           | OSS, EE, and Cloud composition, dependency direction, contribution conflicts, configuration, and final schema assembly                      |
| [03 Storage](03-storage.md)                                                                           | Relational, Redis-compatible, object, and mounted-filesystem capabilities and local/network semantics                                       |
| [04 Relational Schema](04-relational-schema.md)                                                       | Final distribution metadata, migration authority, compatibility, application, and failure semantics                                         |
| [05 HTTP Ingress and Request Contract](05-http-ingress-and-request-contract.md)                       | Role surfaces, request context, proxy and browser trust, authentication boundaries, errors, streaming, and drain                            |
| [06 Durable Operations and Outbox](06-durable-operations-and-outbox.md)                               | Conditional mutation, idempotency evidence, atomic durable commits, outbox publication, retries, and unknown outcomes                       |
| [10 Identity and Access Management](10-identity-and-access-management.md)                             | Organization and Workspace tenancy, User and Service Account identity, credentials, RoleBindings, authorization, and audit                  |
| [11 Secret Management](11-secret-management.md)                                                       | Managed Secret identity, ownership, metadata-only API, encrypted persistence, mutation, deletion, and disclosure controls                   |
| [12 Agent Management](12-agent-management.md)                                                         | AgentPreset identity, immutable Versions, lifecycle, invocation, Plugin management, and reconstruction                                      |
| [13 Interactions, Turns, and Attempts](13-interactions-turns-and-attempts.md)                         | Interaction-to-runtime mapping, Agent tool dispatch evidence, Harness Run binding, active control, and unknown outcomes                     |
| [14 Durable Turn State](14-turn-persistence.md)                                                       | Turn identity, lifecycle, lineage, deterministic state object, conditional checkpoints, sealing, recovery budget, and retention             |
| [15 Durable Turn Attempt Persistence](15-turn-attempt-persistence.md)                                 | TurnAttempt allocation, relational shape, leases, fences, dispatch evidence, transactional takeover, recovery, and Attempt outcomes         |
| [16 Scheduling, Workers, and Recovery](16-scheduling-workers-and-recovery.md)                         | Worker scans, claims, expired-lease takeover, post-claim checks, stale-worker rejection, retry, and shutdown                                |
| [17 Lifecycle and Stream Persistence](17-lifecycle-and-stream-persistence.md)                         | Lifecycle-event persistence, stable Turn presentation Stream, control-Stream separation, bounded live replay, Items, and replay snapshots   |
| [18 Async Subagents](18-async-subagents.md)                                                           | Independent child Threads and Turns, durable relationships, result delivery, and cancellation policy                                        |
| [19 Environment Configuration and Runtime Mounts](19-environment-management.md)                       | Connection revisions, state-owned desired mounts, fresh attachments, Host-retained runtime, active-run keep-alive, and envd boundary        |
| [20 Events, Usage, and Delivery](20-events-usage-and-delivery.md)                                     | Harness observation, AG-UI and Item projection, lifecycle events, delivery, raw usage, large content, and telemetry                         |
| [20a Hook Notifications](20a-hook-notifications.md)                                                   | Hook registry, durable subscriptions, Webhook delivery, channel eligibility, and blocking semantics                                         |
| [21 Management API](21-management-api.md)                                                             | Public resource catalog, common command boundaries, read models, replay, and compatibility                                                  |
| [22 Agent Interaction Retrieval](22-agent-interaction-retrieval.md)                                   | Agent-facing authorized retrieval of retained Turn lineage and interaction projections                                                      |
| [23 Connectors, Connections, and Triggers](23-connectors-connections-and-triggers.md)                 | Trusted Provider discovery, Connector revisions, account Connections, managed tools, and Trigger occurrence acceptance                      |
| [24 Durable Thread Persistence](24-thread-persistence.md)                                             | Thread relational identity, Session membership, origin, version, current Turn, continuation head, creation, advancement, and reads          |
| [25 Model Management](25-model-management.md)                                                         | Workspace ModelConfigs, trusted provider registry, credentials, testing, lifecycle, and Turn-time execution snapshots                       |
| [26 Harness Plugin Artifacts and Runtime Loading](26-harness-plugin-artifacts-and-runtime-loading.md) | Trusted Wheel publication, durable Runtime mode, on-demand loading, Runner cutover, and historical reconstruction                           |
| [27 Skill Management](27-skill-management.md)                                                         | Workspace Skills, ZIP/GitHub import, immutable revisions, public APIs, object storage, AgentPresetVersion locks, and Worker materialization |
| [28 Protocol Gateway](28-protocol-gateway.md)                                                         | Native, Hosted AG-UI, and A2A composition over one Foundation application and authorization boundary                                        |
| [29 Native Streaming and Notifications](29-native-streaming-and-notifications.md)                     | Turn SSE, Workspace lifecycle event reads, and best-effort Native notification WebSocket                                                    |
| [30 Hosted AG-UI](30-hosted-ag-ui.md)                                                                 | AG-UI input authority, external bindings, Turn mapping, event visibility, SSE replay, and cancellation                                      |
| [31 A2A](31-a2a.md)                                                                                   | A2A 1.0 HTTP+JSON discovery, Context/Task projection, streaming, Artifacts, push notifications, and security                                |
| [32 Service SDKs and Clients](32-service-sdks-and-clients.md)                                         | Python, Go, Rust, and TypeScript SDK parity plus Foundation Web and remote CLI boundaries                                                   |
| [33 Agent Input](33-agent-input.md)                                                                   | Versioned Agent input, binary acquisition and delivery, accepted canonicalization, adapter configuration, and Harness mapping               |
| [34 Agent Control: Input and Continuation](34-agent-control-input-and-continuation.md)                | Start, selected-head, null-head root-like, or explicit historical same-Thread continuation, atomic waiting feedback, fork, and retry        |
| [35 Agent Control: Active Execution](35-agent-control-active-execution.md)                            | Thread inbox, durable steer and interrupt commands, state-coupled consumption, Redis control wakeups, and outcome races                     |
| [36 Agent Control: Queued Submissions](36-agent-control-queued-submissions.md)                        | Queue-if-busy Thread submission, editable ordered input, state-first completed handoff, terminal recovery drain, and atomic consumption     |

Read `00`, `01`, and `02` before changing process startup, roles, or distribution
contents. Read `03`, `04`, and `06` before introducing a durable capability.
Read `05`, `10`, and `21` before changing public ingress. Read `24` before `13`
through `17` when changing Thread or Turn persistence, recovery, or reads. Read
`12`, `14`, `19`, and `25` before `33` when changing Agent input. Read
`14` and `15` before `34`, `35`, or `36` when changing Agent invocation,
continuation, queued submission, atomic waiting feedback, fork, retry, steer,
interrupt, or durable control state.
Read `18` before changing async subagents.
Read `06`, `17`, `20`, and `20a` before changing Hook names,
subscriptions, replay, Webhook delivery, or live notification
semantics.
Read `23` before changing Connector Providers, Connections, managed Connector
tools, or Trigger ingress. Read `25` before changing ModelConfigs, Model
Providers, model credentials, or Turn-time model selection. Read `26` with `12`
and `16` before changing managed Harness plugin artifacts, process-local loading,
or Worker compatibility. Read the shared interaction model before changing
Session, Thread, Turn, or Item semantics. Read the Environment Provider and Agent
Stream Protocol catalogs before adding provider or event adapters.

Read `28` before changing any public protocol adapter. Read `29` with `17`,
`20`, and `21` before changing Native streams or notifications. Read `30` with
the Agent Stream Protocol owner before changing Hosted AG-UI. Read `31` before
changing A2A discovery, Tasks, streaming, or push delivery. Read `32` before
changing an SDK, Foundation Web network boundary, or remote CLI operation.

Read `27` with the shared [Managed Skill Package
Contract](../managed-skill-packages.md), `12`, `16`, and the Harness Skill contract
before changing managed Skill upload, revision selection, Worker materialization,
or model-facing exposure.

## Implementation Orientation

The current package establishes these service-wide roots:

| Path                                                             | Architectural role                                                                     |
| ---------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `packages/foundation-service/a13n_service/settings.py`           | Maps process environment into typed provider and migration configuration               |
| `packages/foundation-service/a13n_service/app.py`                | Owns FastAPI lifespan, constructs one storage resource set, and exposes readiness      |
| `packages/foundation-service/a13n_service/storage/`              | Generic backend configuration, construction, lifecycle, and capability semantics       |
| `packages/foundation-service/a13n_service/database/metadata.py`  | Explicit common registry selected by the OSS distribution descriptor                   |
| `packages/foundation-service/a13n_service/database/migration.py` | Programmatic Alembic runner and bounded migration coordination                         |
| `packages/foundation-service/a13n_service/database/migrations/`  | OSS distribution revision location assembled into its final graph                      |
| `packages/foundation-service/a13n_service/cli.py`                | Stable `foundation-service serve` and `foundation-service db ...` executable interface |

These roots are boundaries, not a requirement that every capability become a subpackage. Small capabilities remain focused modules; a capability gains a subdirectory only when it owns several cohesive implementations or contracts. Runnable configuration and migration usage live in the [Foundation Service package guide](../../packages/foundation-service/README.md).

## Authority Rules

- The control role accepts resources and commands and commits exact revisions,
  model snapshots, and state-owned Environment execution configuration. Durable
  Turn lifecycle authority remains in the relational Turn and Thread rows.
- Each Worker uses the deployment's persisted Plugin Runtime mode. An on-demand
  Worker preflights exact Plugin locks before claim; a runner Supervisor starts
  lock-scoped children. The selected loop transactionally claims or replaces
  fenced `TurnAttempt` leases, performs recovery checks, and invokes Harness. It
  exposes no additional product API and never runs migrations.
- PostgreSQL is authoritative for accepted lifecycle state and fencing. Real Redis is required for distributed data flow and coordination; each owning domain defines its Redis retention and replay semantics, and Redis delivery alone never proves a relational transition.
- Queued input remains a separate editable relational resource until one short
  consumption transaction accepts its Turn. Queue rows own no Worker lease,
  retry, or outcome state.
- The artifact's distribution descriptor explicitly composes the complete configuration, routers, role components, authorization contributions, metadata, and migration graph; installed packages never change the service implicitly.
- Foundation records contain only Foundation-owned serializable data. They contain no Python class, plugin instance, native Model, Toolset, Capability, callable, client, credential, Environment attachment, runtime mount, opaque `mount_id`, or live `EnvironmentRuntime`.
- The worker verifies exact locks and uses trusted installed adapters to reconstruct a process-local Harness `AgentDefinition` and fresh `RunBindings`.
- In the default on-demand profile, AgentPresetVersion binds exact PluginVersions
  and Workers load them before claim. In runner mode, a PluginVersion becomes
  active only through explicit deployment administration. Turn acceptance pins
  the resulting exact Runtime lock in either profile.
- A managed Skill becomes usable only through an exact Workspace Skill revision
  lock in an AgentPresetVersion. The selected Worker execution loop verifies its immutable object and supplies
  run-local materialization through the fresh Environment before model exposure;
  GitHub and upload sources are never runtime inputs.
- Connector Provider package presence grants no trust. AgentPresetVersions freeze tool contracts and exact Provider dependency locks; every TurnAttempt resolves current Connection authority and credentials.
- Trigger ingress deduplicates one source occurrence into one root Turn under the common Session and Thread contract. It does not bypass Agent, IAM, scheduling, or Turn authority.
- Foundation Environment connectors return the canonical runtime attachment and
  Foundation consumes `HarnessAguiObserver`; it does not create parallel
  attachment or Harness-event models, and it owns no Environment resource state,
  lifecycle operation, connection lease, or reconciliation workflow.
- A stale TurnAttempt cannot mutate Thread current/head selection, Turn lifecycle or state, pending work, retained Items, child delivery, or terminal outcome. A late immutable `UsageRecord` can still be ingested under its original TurnAttempt when record identity and content validate, but it cannot mutate lifecycle.
- Before an Agent tool call is dispatched, the worker durably records its
  invocation identity and bounded request summary. After a replacement Attempt
  owns the lease, its Worker projects unmatched records as `unknown_outcome` and
  never automatically replays them. One such outcome does not itself trigger
  Attempt replacement.
- Harness completion, durable Turn sealing, Item projection, event delivery, external delivery, usage ingestion, and any external settlement are separate facts.
- Native, Hosted AG-UI, and A2A adapters call the same Foundation application
  authority; their external identifiers and deliveries never replace current
  IAM, Turn, or Thread authority.

## Specification Conventions

- Python-like schemas are conceptual unless explicitly declared as API or storage formats.
- An `AgentPresetVersion` is immutable; changing materialized Agent content,
  `model_id`, concrete Harness model characteristics, native model settings, or
  a managed-resource reference creates another Version. Editing a ModelConfig
  affects only newly accepted Turns.
- `Ref` values identify entities or revisions and grant no authority.
- Process-local objects are reconstructed and never become durable payloads.
- Domain schemas, repositories, queue messages, and events live in their owning domain rather than the generic storage substrate.
