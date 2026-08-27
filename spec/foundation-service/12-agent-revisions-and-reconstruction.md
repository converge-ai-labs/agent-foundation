# Agent Revisions and Reconstruction

## Design Position

Foundation stores serializable Agent authoring resources and immutable executable revisions. It does not persist a Harness `AgentDefinition`, Python import target, plugin instance, native Model, Toolset, Capability, callable, client, credential, or provider binding. A worker reconstructs those process-local values through trusted installed adapters after verifying every selected revision and lock.

A Turn selects exact immutable inputs at durable acceptance. It never resolves `latest` after acceptance or silently adopts edits made while accepted, running, waiting, or resuming after worker loss. Every Foundation-managed Agent invocation has this Turn boundary; Foundation defines no separate durable Agent-work identity.

## Authoring Model

An `Agent` is a stable Workspace-owned resource for authoring, policy, and invocation. It is an authorization target, not an IAM Principal. Its versioned mutable metadata selects one published immutable `AgentRevision`. An `AgentPreset` is a reusable typed authoring input; it is not an executable object and does not override an Agent revision after materialization.

An `AgentRevision` is an independently addressable immutable executable revision associated with one Agent. Its opaque `AgentRevisionId` is the canonical durable reference used by Turns, Triggers, events, and APIs. `agent_id` and positive integer `version` place the revision in its owning Agent lineage but are not a second reference form:

```python
class AgentRevisionRef:
    id: AgentRevisionId
    agent_id: AgentId
    version: int
```

Resolving an Agent ID and version returns this exact reference or fails; it never manufactures a different identity. The complete revision contains only Foundation-owned serializable data and exact references, including:

- logical Agent instructions and typed input/output declarations;
- one exact `ModelIntegrationRevisionId`, concrete Harness model configuration, and concrete native model settings;
- Capability, Tool, Skill, Connector, and Environment declarations under their owning Foundation schemas;
- non-secret Secret requirements that bind an exact Workspace-owned Secret reference or declare an invoking-User Secret key under [Secret Management](11-secret-management.md);
- direct trusted adapter keys and bounded adapter configuration;
- optional Harness plugin configuration under the Harness-owned document contract;
- exact dependency, package, content-digest, and schema compatibility locks.

A `ModelIntegration` is a stable Workspace or Organization resource describing a trusted model-provider integration. A `ModelIntegrationRevision` is independently addressable by `ModelIntegrationRevisionId`, is immutable, and selects exact provider type, routing configuration, supported model surface, compatibility facts, and non-secret credential references. The selected ID belongs to the `AgentRevision`; a Turn cannot override or duplicate that selection. Hosted profiles that use logical model aliases require an explicit `RunModelResolver` and fail closed rather than delegating to ambient native inference.

Secret requirements never contain a Secret value. A Workspace-owned requirement stores the exact Secret resource reference. A User-owned requirement stores only the validated key resolved for the active invoking User; a Service Account cannot satisfy it. Connector declarations contain no credential or Provider-private state. Current Secret eligibility, Connection status, values, credentials, RoleBindings, and run grants are resolved freshly rather than captured in the immutable revision.

A Foundation authoring API may accept Harness model-configuration or model-settings aliases, including deployment-specific private aliases, as convenience input. Materialization selects the model integration first, resolves each alias plane immediately in declaration order, applies explicit concrete overrides last, validates the resulting lifecycle configuration and provider settings under Foundation schemas, and stores only those concrete values. Alias keys are not revision fields, dependency locks, worker inputs, or reconstruction fallback instructions. Unknown aliases and aliases incompatible with the selected provider fail before revision creation. A context budget does not alter the provider model capability, and a native max-output value remains subject to the selected integration's compatibility validation.

Changing materialized Agent content, a selected integration revision, or a dependency lock creates another Agent revision. Prior revisions selected by retained Turns remain addressable for their documented retention period.

The revision boundary exists to prevent accepted or waiting work from changing underneath the worker. For example, a Builder can materialize an `AgentRevision` at version `7` from exact Preset, Model Integration, Tool, Skill, Connector, and Environment revisions, submit its opaque `AgentRevisionId` with a Turn, and then change the Agent's authoring head before a worker claims its first TurnAttempt. The worker still reconstructs that exact revision ID; it never reads the newer mutable head or resolves a current default.

## Revision Relationships

```mermaid
flowchart LR
    Preset[AgentPreset version] --> Materialize[Foundation materialization]
    Agent[Agent] --> Materialize
    Model[ModelIntegrationRevision] --> Materialize
    Resources[Skill, Connector, Tool, and Environment revisions] --> Materialize
    Secrets[Non-secret Secret requirements] --> Materialize
    Locks[Dependency and content locks] --> Materialize
    Materialize --> Revision[Immutable AgentRevision]
    Revision --> Turn[Turn exact selection]
    Turn --> Verify[Worker verification]
    Verify --> Adapter[Trusted reconstruction adapters]
    Adapter --> Definition[Process-local AgentDefinition]
```

Materialization validates resource scope, references, schemas, permission to bind each resource, Secret requirement form, dependency compatibility, and all required locks before committing the immutable revision. The revision records identity and compatibility, not live authority. Current credentials, RoleBindings, run grants, provider availability, Secret eligibility, and Environment bindings are resolved freshly for every `TurnAttempt` and Harness Run.

## Dependency Locks

A dependency lock identifies every package, external content unit, adapter, or schema whose change could alter reconstruction, Capability behavior, state compatibility, security, or output semantics. It includes exact package or content identities, trusted adapter keys, selected Connector Provider artifacts, relevant schema or codec compatibility, and integrity digests when content is externally materialized.

Package installation or entry-point availability grants no trust. The deployment selects allowed adapter and plugin keys, verifies the exact lock, and imports only those installed targets. A durable row never contains an arbitrary module, class, file path, shell command, or remote code URL for execution.

An adapter replacement can reconstruct a retained revision only when it explicitly declares compatibility with that revision and its locks. Name similarity, newer package versions, or a current default does not satisfy a missing lock.

## Worker Reconstruction

```mermaid
sequenceDiagram
    participant Worker
    participant Store as Foundation store
    participant Adapter as Trusted adapter
    participant Harness

    Worker->>Store: read Turn, exact AgentRevision, and dependency locks
    Worker->>Worker: verify scope, locks, compatibility, and TurnAttempt generation
    Worker->>Adapter: reconstruct native Agent inputs
    Adapter-->>Worker: AgentSpec with concrete settings, Model selection, Capabilities, plugins, and policies
    Worker->>Worker: create fresh Identity, policy, credential, model, and provider attachments
    Worker->>Harness: HarnessBuilder with process-local values
    Harness-->>Worker: ExecutableAgent
```

Reconstruction is deterministic with respect to the revision and declared locks, while live authority and provider reachability are intentionally fresh. The worker assigns a stable `AgentInstanceRef` to each independently advancing root, delegated child Agent, or fork history. A replacement worker preserves that reference for the same Thread, but uses a new `TurnAttempt`, transient Harness Run correlation, and fresh bindings.

The worker validates the complete definition before starting model or tool work. It reconstructs Harness `ModelConfiguration` and native `ModelSettings` directly from the revision's concrete values and performs no alias lookup. It does not partially execute a revision whose output schema, Capability state codec, plugin contract, model integration, Environment provider, or dependency lock is incompatible.

## Continuation Compatibility

A Thread preserves one independently advancing Agent lineage. A new Turn that continues the Thread sets `parent_turn_id` to the exact sealed previous Turn and initializes from that parent's state. Selecting another Agent revision for the new Turn is allowed only when the new revision explicitly accepts the parent's Agent, Capability, output, Environment-state, and plugin compatibility facts. Otherwise the caller creates an explicit fork with a new lineage and no implied state migration.

Editing an Agent or publishing another revision never mutates an existing Turn, Item, state checkpoint, pending fact, or asynchronous child relationship. A later TurnAttempt for the same Turn reconstructs the revision selected when the Turn was accepted. A new continuation, fork, or retry Turn can select another revision only through an explicit authorized request and compatibility check.

## Failure Semantics

| Failure                                 | Outcome                                                                     |
| --------------------------------------- | --------------------------------------------------------------------------- |
| Missing revision or lock                | Turn fails before Harness construction                                      |
| Content digest or package lock mismatch | Turn fails closed and records bounded incompatibility evidence              |
| Unknown adapter or plugin key           | Revision is not reconstructed; no ambient import fallback occurs            |
| Required `RunModelResolver` unavailable | Turn fails before native model inference                                    |
| Credential or policy unavailable        | Fresh binding fails; the immutable revision is not rewritten                |
| Checkpoint incompatible with revision   | Continuation fails before Harness entry; display history is not substituted |
| Worker lost during reconstruction       | Lease recovery uses a new generation; no process-local object is restored   |

## Invariants

1. A Turn stores one exact `AgentRevisionId`; the selected revision owns its exact integration revisions and the worker never resolves a mutable Agent head at claim time.
2. A revision contains serializable Foundation data and references only, never live Python objects or credentials.
3. Dependency locks and content digests are verified before Harness construction.
4. Package presence does not authorize an adapter, plugin, Capability, provider, or import target.
5. Every TurnAttempt reconstructs fresh authority and bindings without mutating the selected revision.
6. Replacement workers preserve stable Thread identity and change TurnAttempt generation and transient Harness Run correlation.
7. A retained checkpoint is used only under explicitly compatible Agent and state contracts.
8. Connector tool contracts and Provider artifacts are frozen by the Agent revision; Connection authority and credentials remain fresh per TurnAttempt.
