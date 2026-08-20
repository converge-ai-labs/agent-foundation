# Harness Domain Model

## Design Position

The harness domain describes one process-local Pydantic AI execution, the Agent instance that performs it, and the state and observations produced by it. Durable definitions, executions, attempts, leases, queues, and recovery remain host domains.

```mermaid
erDiagram
    AGENT_DEFINITION ||--|| RESOLVED_AGENT_DEFINITION : materializes
    RESOLVED_AGENT_DEFINITION ||--o{ HARNESS_RUN : executes
    AGENT_IDENTITY_REF ||--o{ AGENT_INSTANCE_CONTEXT : identifies
    AGENT_INSTANCE_CONTEXT ||--o{ HARNESS_RUN : carries
    HARNESS_RUN ||--|| AGENT_CONTEXT : owns
    AGENT_CONTEXT ||--|| BOUND_PLUGIN_CONTEXT : indexes
    BOUND_PLUGIN_CONTEXT ||--o{ RUN_BOUND_PLUGIN : contains
    AGENT_CONTEXT ||--o{ CAPABILITY_STATE : namespaces
    CAPABILITY_STATE ||--o| ENVIRONMENT_STATE : may_encode
    CAPABILITY_STATE ||--o| DELEGATION_STATE : may_encode
    HARNESS_RUN ||--o| HARNESS_STATE : exports
    HARNESS_RUN ||--o{ TOOL_INVOCATION : invokes
    HARNESS_RUN ||--o{ SUBAGENT_INVOCATION : delegates
    HARNESS_RUN ||--o{ HARNESS_EVENT : emits
    HARNESS_RUN }o--|| RUN_USAGE : accumulates_into
    BOUND_ENVIRONMENT ||--o{ HARNESS_RUN : serves
```

## Ownership

| Concept                   | Meaning                                                                                                                                                                        | Owning document                                                          |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------ |
| `AgentDefinition`         | Complete materialized logical Agent definition containing an upstream `AgentSpec`, Harness plugin specs, Environment requirements, and child definitions.                      | [Agent Definition and Build](03-agent-definition-and-build.md)           |
| `ResolvedAgentDefinition` | Immutable process-local build plan containing the definition plus selected plugin catalog and resolved model, tool, Toolset, Capability, output, child, and provenance inputs. | [Agent Definition and Build](03-agent-definition-and-build.md)           |
| `AgentIdentityRef`        | Stable workload identity supplied by the trusted host.                                                                                                                         | This document                                                            |
| `AgentInstanceRef`        | Stable non-authoritative logical instance owner reference preserved across a continuation.                                                                                     | This document                                                            |
| `AgentInstanceContext`    | Fresh root or child run binding containing instance identity, actor, lineage, and opaque Host correlation.                                                                     | This document                                                            |
| `AgentContext`            | Typed Pydantic AI dependency, multi-Environment holder, run-bound plugin index, and Capability-namespaced run-state center.                                                    | [Capability and Agent Context Model](04-capability-model.md)             |
| `BoundPluginContext`      | Immutable ID-and-type index of the fresh plugins bound to one Harness run.                                                                                                     | [Harness Plugin System](05-plugin-system.md)                             |
| Harness run               | One process-local Harness plugin chain around at most one Pydantic AI execution.                                                                                               | [Execution Context and Lifecycle](06-execution-context-and-lifecycle.md) |
| `HarnessRunStream`        | Single-consumer event, terminal-result, and live-control facade for a streamed harness run.                                                                                    | [Public API and Packaging](14-public-api-and-packaging.md)               |
| `HarnessState`            | Minimal continuation state exported for a later run.                                                                                                                           | [Harness State and Resume](10-snapshot-and-resume.md)                    |
| `ToolInvocation`          | One normalized call through the common Pydantic tool wrapper path.                                                                                                             | [Tool Execution](07-tool-execution.md)                                   |
| `SubagentInvocation`      | One blocking inline child call whose continuation is owned by Delegation State.                                                                                                | [Delegation and Subagents](11-delegation-and-subagents.md)               |
| `BoundEnvironment`        | Identity-bound facade over host-selected Environment bindings.                                                                                                                 | [Environment Integration](08-environment-integration.md)                 |
| `HarnessEvent`            | Typed observation of one run.                                                                                                                                                  | [Events, Observability, and Usage](12-events-observability-and-usage.md) |
| Pydantic `RunUsage`       | Live usage accumulator, optionally shared by an inline execution tree and copied into each result.                                                                             | [Events, Observability, and Usage](12-events-observability-and-usage.md) |

## Agent Identity

```python
class AgentIdentityRef(BaseModel):
    issuer: str
    subject: str
```

The identity is stable across Agent definition revisions. It names the workload principal but does not carry policy or credentials.

```python
class AgentInstanceRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    identity: AgentIdentityRef
    agent_instance_id: str


class AgentInstanceContext(BaseModel):
    identity: AgentIdentityRef
    agent_instance_id: str
    parent_agent_instance_id: str | None
    delegation_id: str | None
    actor: ActorRef | None
    host_refs: Mapping[str, str]

    def ref(self) -> AgentInstanceRef: ...
```

`AgentInstanceRef` is the stable non-authoritative owner and instance selector for one logical Agent instance. A Host that resumes one Execution preserves it across Attempt-local contexts; a new root or child instance receives another value. Parent and delegation lineage stay in fresh trusted `AgentInstanceContext` and Host records rather than being encoded in the ref. The ref contains no actor, run, Attempt, credential, policy, or live Host handle. Task ownership serializes it, while every mutation still derives the current actor from fresh trusted context and Host policy.

`actor` records who triggered the work. `host_refs` contains opaque Host correlation such as an Execution, request, or job reference. Neither field grants authority. `ref()` is derived by trusted code and never accepts a model-supplied identity or owner string.

A root execution starts with a new Agent instance. An inline child receives another stable instance ID and explicit parent lineage; its stored ID selects the corresponding child continuation but never restores authority. A later run can preserve the same instance context when the Host treats it as a continuation. A Host-managed asynchronous child also receives explicit child lineage, but its execution identity, lifecycle, and result delivery are Host records rather than `SubagentInvocation` or Harness State.

## Process-local and Host-owned State

```mermaid
flowchart LR
    HostSource[Host definition source] --> Revision[Materialized definition revision]
    Revision --> Resolved[ResolvedAgentDefinition]
    HostExecution[Host execution, attempt, or job] -. opaque ref .-> Instance[AgentInstanceContext]
    Resolved & Instance --> Run[Harness run]
    Previous[HarnessState] -. optional .-> Run
    Run --> Result[Harness result]
    Run --> State[HarnessState]
    Result & State --> HostCommit[Host-owned persistence and lifecycle]
```

The harness sees host versions and execution entities only through resolved content or opaque references. A process-local result does not commit a host-owned durable execution, and exported state does not identify the authoritative durable checkpoint.

## Version Boundaries

| Version                  | Meaning                                                                    | Owner                     |
| ------------------------ | -------------------------------------------------------------------------- | ------------------------- |
| `HarnessState` version   | Continuation envelope shape                                                | Harness                   |
| capability state version | One capability's serialized entry                                          | Capability implementation |
| Environment generation   | Validity of provider handles and cursors                                   | Environment provider      |
| host definition revision | Immutable materialized definition, Preset provenance, and dependency locks | Host                      |
| identity policy version  | Authorization ceiling and revocation semantics                             | Host policy provider      |

The versions stay independent because a model configuration update, a capability state migration, an Environment restart, and a policy revocation have unrelated effects.

## Boundaries

- `AgentDefinition` is the canonical materialized logical definition and embeds Pydantic AI configuration plus Harness plugin specs without translating or replacing upstream model-loop fields.
- Host Presets produce immutable `AgentDefinition` snapshots before execution; they are not process-local inheritance or run state.
- `ResolvedAgentDefinition` adds the selected plugin catalog and process-local native model, tool, Toolset, Capability, output, child, and provenance values without becoming a durable format.
- A harness run is process-local and has no durable attempt or lease semantics; `HarnessRunStream` adds no durability or event replay.
- `AgentContext` is the single mutable state center for one run; `BoundPluginContext` is an immutable typed index rather than a state store, and Capabilities use namespaced entries for continuation.
- `HarnessState` contains Pydantic `message_history` and the complete recoverable `AgentContextState`, including versioned Environment, parent-owned Working State, and Delegation entries with nested child `HarnessState` values, but no host lifecycle, active child job, or live provider object.
- Agent Identity enters from the host and remains distinct from actor, run, host execution, Environment, and OS user identities.
- Events and terminal `RunUsage` snapshots leave the harness as observations; hosts decide persistence, cross-run aggregation, and cost policy.

## Trade-offs

### Small shared model

Keeping only Identity and cross-document relationships here avoids a central mega-schema. Readers follow owning documents for details, while consistency depends on maintaining stable names and links.

### Opaque host references

Opaque references preserve reuse across embedded and hosted execution. The harness cannot inspect host lifecycle directly, which is intentional because lifecycle authority stays with the host adapter.

### Stable identity, transient runs

A stable workload identity supports policy and credentials across definition revisions. Treating each Pydantic execution as a new run keeps resume simple and avoids duplicating Host attempt semantics.
