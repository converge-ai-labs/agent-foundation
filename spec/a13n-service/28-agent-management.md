# Agent Management

## Design Position

Service exposes `Agent` as the stable Workspace-owned identity for authoring, authorization, lifecycle, and invocation. Each `AgentRevision` is one immutable authoring configuration with frozen stable-resource bindings. A Run resolves that policy into one exact immutable `EffectiveAgentConfig`. The Agent head contains metadata, lifecycle state, a canonical `version`, and `current_revision_id`; it contains no mutable draft configuration.

Creating an Agent atomically creates Revision v1. Creating a genuinely different Revision appends immutable content and advances the Agent and current Revision to the same next `version`. Metadata and lifecycle mutations use strong ETags and do not change that version. Service exposes no `AgentPreset` compatibility resource or independently mutable default-Revision pointer.

A Run selects one exact `AgentRevision` at durable acceptance. Its current `RunAttemptExecutor` reconstructs process-local Harness `AgentDefinition`, `SubagentDefinition`, `ExecutableAgent`, and Plugin objects from the Run's complete frozen `EffectiveAgentConfig` graph. Those Python values are never management resources or durable payloads.

[Managed Harness Plugins and Runtime](36-managed-harness-plugins-and-runtime.md) owns Plugin and PluginVersion identity, selection, lifecycle, commands, artifacts, and Runtime behavior. Agent Management embeds only typed Plugin selections and exact resolved locks into Agent configuration and Revisions.

```mermaid
flowchart LR
    Config[Complete AgentConfig] -->|Create or create Revision| Revision[Immutable AgentRevision]
    Revision -->|Advance atomically| Current[Agent current Revision]
    Plugins[Managed Plugin selections] --> Revision
    Current --> Acceptance[Implicit Run selection]
    Revision -->|Frozen bindings and policy| Acceptance
    Override[Typed AgentRunOverride] --> Acceptance
    Acceptance --> Run[Persisted Run with exact Revision and effective config]
    Run -->|Pins lock digest| Worker[Worker or Runner execution process]
    Worker --> Definition[Process-local AgentDefinition graph]
    Definition --> Harness[Agent Harness]
```

## Boundaries

| Concern                                                                           | Owner                                                                                                                                                                                                                                                               | Relationship                                                                |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| Agent identity, Revisions, current selection, lifecycle, Duplicate, and overrides | This document                                                                                                                                                                                                                                                       | Defines the durable Agent management model                                  |
| Plugin identity, Versions, selection, lifecycle, commands, artifacts, and Runtime | [Managed Harness Plugins and Runtime](36-managed-harness-plugins-and-runtime.md)                                                                                                                                                                                    | Supplies typed selections and exact resolved locks to Agents                |
| Plugin factories, configured instances, middleware, and Capability contribution   | [Harness Plugin System](../a13n-harness/05-plugin-system.md)                                                                                                                                                                                                        | Builds concrete process-local plugins from an explicitly selected catalog   |
| Agent execution, state, and process-local subagent graph                          | Agent Harness                                                                                                                                                                                                                                                       | Receives reconstructed definitions and fresh run bindings                   |
| Run identity, acceptance, persistence, lineage, and recovery                      | [Agent Interaction and Execution Model](10-agent-interaction-and-execution-model.md), [Agent Control](18-agent-control-input-and-continuation.md), [Durable Run State](12-run-persistence.md), and [RunAttempt Recovery](13-run-attempt-scheduling-and-recovery.md) | Persist and reuse the exact selected Revision and effective config          |
| Agent input wire, canonicalization, and adapter mapping                           | [Agent Input](17-agent-input.md)                                                                                                                                                                                                                                    | AgentConfig stores adapter configuration and each Revision freezes it       |
| Managed capability and ConnectorConnection schemas                                | Their owning Skill and [Connectivity](40-connectivity/README.md) contracts                                                                                                                                                                                          | Agent configuration and Run overlays reference them without redefining them |
| Product authorization and executable-code administration                          | [Service IAM](33-identity-and-access-management.md)                                                                                                                                                                                                                 | Separates Agent authoring from deployment code authority                    |
| Secret values and run-time eligibility                                            | [Secret Management](27-secret-management.md)                                                                                                                                                                                                                        | Revisions store requirements and references, never plaintext values         |
| Public HTTP paths and common mutation behavior                                    | [Management API](16-management-api.md) and [Platform API Conventions](../api-conventions.md)                                                                                                                                                                        | Expose the resources and commands defined here                              |

## Agent Resource Model

The following schemas are conceptual. They define durable field meaning rather than concrete ORM classes.

```python
class Agent:
    id: AgentId
    organization_id: OrganizationId
    workspace_id: WorkspaceId
    source: Literal["builtin", "custom"]
    name: str
    description: str | None
    version: int
    current_revision_id: AgentRevisionId
    default_environment_template_id: EnvironmentTemplateId | None
    enabled: bool
    archived_at: datetime | None
    duplicated_from_agent_id: AgentId | None
    duplicated_from_revision_id: AgentRevisionId | None
    created_by: ActorRef
    updated_by: ActorRef
    created_at: datetime
    updated_at: datetime
```

`Agent.version` starts at `1` and always equals the current `AgentRevision.version`. It advances only when a genuinely new immutable Revision becomes current. `current_revision_id` is always present; Service never exposes an Agent without an executable Revision.

`name`, `description` and `default_environment_template_id` are mutable head metadata. The template default only seeds new Thread Environment allocation under [Environment Management](29-environment-management.md#thread-defaults-and-run-selection); it never changes an existing Thread or Run and does not publish an AgentRevision. `enabled` and `archived_at` are independent lifecycle axes. Their mutations change `updated_at` and the representation ETag without advancing `version` or rewriting a Revision.

## AgentConfig

`AgentConfig` is finite Service-owned serializable data. A caller supplies one complete config when creating an Agent or Agent Revision; Service stores no independently editable draft. Referenced resource schemas remain owned by their management documents.

```python
class AgentModel:
    model_key: str
    settings: JsonObject
    characteristics: HarnessModelCharacteristics


class ResolvedAgentModel:
    model_id: ModelId
    model_key: str
    settings: JsonObject
    characteristics: HarnessModelCharacteristics


class EffectiveAgentModel:
    execution: ModelExecutionSnapshot
    settings: JsonObject
    characteristics: HarnessModelCharacteristics


class ConnectorConnectionToolSelection:
    connector_connection_id: ConnectorConnectionId
    tools: tuple[str, ...] | None = None
    defer_loading: bool = False


class MCPConnectionToolSelection:
    mcp_connection_id: MCPConnectionId
    tools: tuple[str, ...] | None = None
    defer_loading: bool = False


class ChildEnvironmentPolicy:
    mode: Literal["none", "shared", "dedicated"]
    template_revision_id: EnvironmentTemplateRevisionId | None = None


class SubagentSelection:
    agent_id: AgentId
    version: int | None
    description: str | None
    context: DelegationContextPolicy
    usage_limits: UsageLimits | None
    environment: ChildEnvironmentPolicy


class OutputVariant:
    name: str
    description: str | None
    schema: JsonSchema
    resources: dict[str, JsonSchema]


class OutputSpec:
    name: str | None
    description: str | None
    schema: JsonSchema | None
    resources: dict[str, JsonSchema]
    variants: tuple[OutputVariant, ...] | None


class RetryConfig:
    tools: int
    output: int


class InputAdapterConfig:
    adapter_key: str
    config: JsonObject


class AssetPublicationConfig:
    enabled: Literal[True]


class ProtocolConfig:
    schema_version: Literal["1"]
    public_name: str
    public_description: str | None
    output_modes: tuple[str, ...]
    input_data_schema: JsonObject | None
    state_schema: JsonObject | None
    context_schema: JsonObject | None
    client_tools: tuple[ClientToolPolicy, ...]
    event_visibility: tuple[str, ...]
    a2a_skills: tuple[A2ASkillProjection, ...]
    extended_agent_card: ExtendedAgentCardPolicy | None
    limits: ProtocolLimits


class AgentConfig:
    subagent_mode: Literal["inline", "async"] = "inline"
    model: AgentModel
    instructions: str
    input_adapter: InputAdapterConfig
    plugins: tuple[PluginSelection, ...]
    skills: tuple[SkillSelection, ...]
    connector_tools: tuple[ConnectorConnectionToolSelection, ...]
    mcp_tools: tuple[MCPConnectionToolSelection, ...]
    subagents: dict[str, SubagentSelection]
    client_tools: tuple[ClientToolDefinition, ...]
    output_spec: OutputSpec | None
    retries: RetryConfig | None
    secret_requirements: tuple[SecretRequirement, ...]
    asset_publication: AssetPublicationConfig | None
    protocol: ProtocolConfig
```

The [`PluginSelection` contract](36-managed-harness-plugins-and-runtime.md#agent-selection-and-revision-locking) determines which variant is legal under the deployment's fixed Runtime profile. `instructions` is the Agent's stable system prompt; Service- and Harness-generated runtime context is not stored in this field. A [`SkillSelection`](31-skill-management.md#agent-selection-and-run-locking) names one stable Skill key and optionally pins an integer version. The model selects one stable Model key. Primary Environment selection is independent Thread/Run context under [Environment Management](29-environment-management.md#thread-defaults-and-run-selection). `ChildEnvironmentPolicy.template_revision_id` is required exactly for `dedicated`; `shared` uses the spawning Run's Environment and `none` supplies no environment. The selected Model owns its one calling API and default request settings. Agent Revision creation resolves and retains stable Model and Skill identities but does not freeze mutable Model configuration or an unpinned Skill's current Revision; every Run resolves those selections under their owning contracts. Subagent map keys are stable local names within the Agent.

`connector_tools` and `mcp_tools` are ordered lists keyed semantically by their managed connection IDs, with no caller-defined aliases. Duplicate connection IDs within either category are invalid. Omitted or null `tools` selects all currently available authorized source tools; an empty list selects none; explicit names select only those source-native tools. Duplicate tool names are invalid. `defer_loading` defaults to false and uses the [Harness loading contract](40-connectivity/04-agent-facing-tools.md#deferred-loading). These fields control one Agent or Run selection rather than the connection resource itself.

`AgentModel.settings` defaults to an empty object and stores only Agent-authored overrides. Its JSON representation is validated against the selected Model's serializable native settings contract, including provider-specific fields, under [Model Management](30-model-management.md#parameter-schemas-and-validation). The same owner defines parameter descriptions, reserved fields, and [settings precedence](30-model-management.md#settings-precedence). `ResolvedAgentModel` retains these overrides, while `EffectiveAgentModel.settings` contains the final merged values used for execution.

`OutputSpec` permits either one top-level schema with optional local resources or at least two mutually exclusive variants; it never permits nested variants. `RetryConfig` contains bounded non-negative tool-argument and structured-output correction budgets, not provider transport, Worker recovery, whole-Run, or business-workflow retries.

The config contains no Python class, import target, callable, native Model, Toolset, Capability instance, Plugin object, client, credential value, plaintext Secret, Environment adapter, attachment session, entered facade, arbitrary artifact URL, or other process-local value. Revision creation is the sole authoritative resolve-and-build validation path.

`input_adapter` selects one trusted adapter key and bounded configuration. Every Revision accepts the common [`AgentInput`](17-agent-input.md) wire contract. Run acceptance validates and canonicalizes that input, and the Worker verifies the pinned Runtime lock before invoking the adapter. Replacement execution attempts reuse the same Revision, adapter configuration, accepted input, and Runtime lock.

When `asset_publication` is present, the trusted Service `AssetCapability` exposes `publish_asset`. Each execution attempt binds only its current authorized Environment; the tool fails closed when no readable default binding can supply the selected path. Package presence or general Environment file access does not enable it.

## Run Capability Overlay

Agent authoring defines the default model-visible managed capability surface. After applying `AgentRunOverride`, a direct invocation or trusted input owner such as an authorized host integration can supply one bounded overlay without mutating the AgentRevision:

```python
class RunCapabilityOverlay:
    inherit_agent: bool = True
    include: tuple[ManagedCapabilitySelection, ...] = ()
    exclude: tuple[CapabilityKey, ...] = ()
```

`ManagedCapabilitySelection` is a tagged union owned by the corresponding managed Skill, MCPConnection, or ConnectorConnection tool contract. ConnectorConnection and MCPConnection entries reuse the tool-selection types above, including `tools` and `defer_loading`. `CapabilityKey` is derived from capability kind and managed source identity, not a caller-defined connection alias. Every selection retains its managed-resource references and compatibility evidence required by its owning contract; external tool schemas are discovered at execution. The overlay contains no Python object, import target, arbitrary local function tool, Plugin, credential, endpoint, or remote schema. Host-injected runtime capabilities are composed separately under their owning execution-context contract; this overlay cannot create or replace them.

```text
candidate = (overridden Agent selections when inherit_agent else empty) + include - exclude
effective = candidate intersect current authorization and deployment policy
```

`include` can select an authorized managed capability absent from the Agent defaults. `exclude` removes one exact selectable key and cannot remove mandatory Harness safety, Identity, policy, usage, output, or Environment behavior. `inherit_agent=false` replaces only the selectable managed capability surface; it does not replace the Agent, model, instructions, output contract, Plugins, subagents, Runtime lock, or security ceiling. Duplicate keys, conflicting selections, unknown exclusions, unavailable compatibility evidence, and unauthorized additions fail Run acceptance.

The accepted Run retains the complete effective selections and the revision locks required by each capability owner. Connection selections retain source identity, tool scope, and deferred-loading policy; [external tool discovery](40-connectivity/04-agent-facing-tools.md#discovery-and-recovery) supplies current schemas without a durable tool snapshot. Replacement RunAttempts preserve those selections and locks while revalidating authority and discovering current external tools. Model input and tool output cannot create or modify an overlay.

## Protocol Configuration

Every `AgentConfig` embeds one finite `protocol` configuration. It is Agent-owned Revision content rather than an independently addressable resource and has no separate lifecycle, API, enable switch, or content digest.

Revision creation validates JSON Schemas, public metadata, MIME modes, event names, client-tool policies, A2A projections, and per-protocol limits against finite registries and deployment hard ceilings. Configuration can narrow a permitted surface but cannot expose raw reasoning, credentials, private execution identities, unregistered events, arbitrary code, or unavailable capabilities.

`input_data_schema`, when present, is the self-contained JSON Schema Draft 2020-12 contract projected for non-null `AgentInput.structured_content`. Safe defaults impose no structured-content schema, expose bounded text output and standard public event families, accept no protocol client tools, generate a minimal public-safe A2A Agent Card, and expose no extended Card. Native and Hosted AG-UI remain available for every callable Agent; `gateway.a2a_enabled` is the deployment-wide A2A availability switch.

Revision creation freezes normalized ProtocolConfig in the immutable Revision, whose `content_digest` covers it. Hosted AG-UI Run and A2A Task acceptance persist the exact `agent_revision_id`; retry, feedback, recovery, and replay therefore use the same protocol configuration. Advancing the Agent to another Revision changes Cards and implicit acceptance policy only for later work.

## AgentRunOverride and Effective Configuration

One Run request may carry a finite typed `config_override`. It is request data, not a management resource, and has no identity or lifecycle:

```python
class ModelOverride:
    model_key: str | None
    settings: JsonObject | None
    characteristics: HarnessModelCharacteristics | None


class SubagentOverride:
    agent_id: AgentId | None
    version: int | None
    description: str | None
    context: DelegationContextPolicy | None
    usage_limits: UsageLimits | None
    environment: ChildEnvironmentPolicy | None


class RetryOverride:
    tools: int | None
    output: int | None


class AgentRunOverride:
    model: ModelOverride | None
    instructions: str | None
    plugins: tuple[PluginSelection, ...] | None
    skills: tuple[SkillSelection, ...] | None
    connector_tools: tuple[ConnectorConnectionToolSelection, ...]  # May be absent.
    mcp_tools: tuple[MCPConnectionToolSelection, ...]  # May be absent.
    subagents: dict[str, SubagentOverride | None] | None
    client_tools: tuple[ClientToolDefinition, ...] | None
    output_spec: OutputSpec | None
    retries: RetryOverride | None
```

The wire schema preserves absent fields separately from explicit nulls. Top-level absence inherits the selected Revision. Scalar and string fields replace; list fields replace as a whole and an empty list clears them. `output_spec` can be explicitly cleared. Primary Environment selection is a separate invocation field and cannot appear in `config_override`. `retries` patches only explicitly present children.

Within `model`, an absent `model_key` inherits the Agent selection; a supplied key selects another managed Model and cannot be null. There is no API override independent of that Model. `settings` follows the Model Management precedence contract, including explicit clearing of Agent overrides and validation against the final selected Model.

`connector_tools` and `mcp_tools` each replace their complete category when present. Absence inherits, `[]` clears, and null for the whole category is invalid. Entries use the same complete selection types as Agent configuration; there is no per-alias patch or mapped deletion. Overrides can select existing authorized connections, tool scopes, and deferred loading, but cannot supply endpoints, credentials, external integration services, arbitrary headers, or native Ingress targets.

`subagents` remains a name-keyed patch: an absent map inherits, explicit null clears all entries, an empty object changes nothing, and a mapped null deletes one entry. Its entries select managed Agents only.

Plugin override replacement and resolution follow the [managed Plugin selection contract](36-managed-harness-plugins-and-runtime.md#agent-selection-and-revision-locking). Every selected resource remains subject to current authorization, schema validation, deployment compatibility, and platform security ceilings. Run overrides select managed resources and accept no direct credential values. The owning resource domain resolves current credentials at its execution boundary.

The resolved non-secret result has this conceptual shape:

```python
class EffectiveAgentConfig:
    subagent_mode: Literal["inline", "async"]
    child_configs: dict[AgentRevisionId, ChildAgentExecution]
    schema_version: str
    model: EffectiveAgentModel
    instructions: str
    input_adapter: InputAdapterConfig
    plugins: tuple[ResolvedPluginVersion, ...]
    runtime_lock_digest: str
    skills: tuple[SkillRevisionLock, ...]
    connector_tools: tuple[ConnectorConnectionToolSelection, ...]
    mcp_tools: tuple[MCPConnectionToolSelection, ...]
    subagents: tuple[ResolvedSubagentEdge, ...]
    client_tools: tuple[ClientToolDefinition, ...]
    output_spec: OutputSpec | None
    retries: RetryConfig | None
    secret_requirements: tuple[SecretRequirement, ...]
    asset_publication: AssetPublicationConfig | None
    protocol: ProtocolConfig
    content_digest: str
```

`subagent_mode` selects one standard Harness Tool surface for the accepted Run. The default `inline` executes the entire descendant graph in the parent Attempt and borrows its Environment facade. `async` delegates each direct child as an independent durable Run; when that child is claimed, its own accepted `subagent_mode` governs its descendants. A single Harness invocation does not mix the inline and asynchronous Tool surfaces. This field is selected by the Agent Revision, not a Run override.

Each `child_configs` entry contains the child `agent_id`, `revision_content_digest`, complete recursive `effective_config`, and frozen `connector_connection_selections` and `mcp_connection_selections`. Its key is the exact child Revision ID from a resolved edge. The parent acceptance prepares the complete finite graph and revalidates all prepared evidence in the final transaction. Each node freezes its own Model execution, merged settings, Skill locks, Plugin lock, and connection selections. The root digest covers these descendant snapshots. A child never substitutes the parent's Model or selectable capabilities. Asynchronous child admission copies the accepted child snapshot and rejects a changed config or connection scope; a retained child continuation preserves its own source snapshot.

Acceptance merges and resolves the selected Revision and request exactly once, then persists a complete immutable `EffectiveAgentConfig` plus its digest. Its Skill entries are the exact five-field [`SkillRevisionLock`](31-skill-management.md#agent-selection-and-run-locking) values selected at that acceptance boundary. Retry, waiting Continue, deferred-action completion, and other successor operations preserve the source snapshot when their owning contract requires it; Worker replacement of the same accepted Run always reuses it. No execution attempt re-reads an Agent or Skill head or reapplies merge rules. Input, Environment selection, attachments, timeout, usage budget, metadata, priority, idempotency, and scheduling mode remain Run fields rather than Agent config overrides.

## Immutable AgentRevision

```python
class ResolvedSubagentEdge:
    name: str
    child_agent_id: AgentId
    child_agent_revision_id: AgentRevisionId
    description: str | None
    context: DelegationContextPolicy
    usage_limits: UsageLimits | None
    environment: ChildEnvironmentPolicy


class AgentRevision:
    id: AgentRevisionId
    organization_id: OrganizationId
    workspace_id: WorkspaceId
    agent_id: AgentId
    version: int
    plugin_runtime_mode: Literal["on_demand", "runner"]
    config: AgentConfig
    config_digest: str
    resolved_model: ResolvedAgentModel
    resolved_plugin_versions: tuple[ResolvedPluginVersion, ...]
    runtime_lock_digest: str
    resolved_skills: tuple[ResolvedSkillBinding, ...]
    connector_tools: tuple[ConnectorConnectionToolSelection, ...]
    mcp_tools: tuple[MCPConnectionToolSelection, ...]
    resolved_subagents: tuple[ResolvedSubagentEdge, ...]
    content_digest: str
    source_revision_id: AgentRevisionId | None
    created_by: ActorRef
    created_at: datetime
```

Revision rows are append-only. `config_digest` identifies the canonical complete authoring config; `content_digest` also covers every resolved snapshot, stable managed-resource binding and selection policy, Runtime lock, and subagent Revision. For an unpinned Skill, it covers `skill_id`, `skill_key`, and the absence of a version, not whichever current SkillRevision a later Run resolves. A Revision has no mutable lifecycle state and cannot be patched, archived independently, deleted, overwritten, or repointed after creation.

`plugin_runtime_mode`, `resolved_plugin_versions`, and `runtime_lock_digest` embed the exact result of the [managed Plugin contract](36-managed-harness-plugins-and-runtime.md#agent-selection-and-revision-locking). The resolved Model field retains only stable Model identity, Agent-authored setting overrides, and characteristics; Run acceptance resolves the latest Model execution selection and effective settings under Model Management. Resolved Skill bindings freeze stable `skill_id` identity and pinned-or-current policy; only a pinned binding identifies versioned content before Run acceptance. The other resolved fields freeze ConnectorConnection and MCPConnection selections, and the complete child Revision graph with exact dedicated Environment template revisions. Secret values, current authorization, current Model and Provider configuration/lifecycle, an unpinned Skill's current Revision, live ConnectorProvider availability, and remote MCP catalogs remain fresh facts rather than immutable Agent Revision content.

## Creation, Revision, and Restore

Create Agent accepts `name`, optional `description`, and one complete `config`. Service authorizes and resolves every referenced dependency, then atomically creates the Agent and Revision v1. It never exposes an Agent without a current Revision.

Create Revision accepts `expected_version` and one complete replacement `config`:

1. authorize the operation and every referenced resource;
2. verify the expected Agent version;
3. resolve exact Model identity, Plugin versions, stable Skill bindings, ConnectorConnection, and subagent dependencies, including dedicated child Environment template revisions;
4. verify schemas, the finite acyclic subagent graph, Plugin evidence, and reconstruction compatibility;
5. canonicalize the frozen content and compute its digests;
6. return the current Agent and Revision unchanged for a semantic no-op; or
7. create immutable version `current + 1` and advance the Agent head in the same transaction.

Resolution and process-local build preflight occur outside an open database transaction. The final short transaction rechecks the Agent, selected references, profile-specific Plugin evidence, authorization, and concurrency evidence before committing. Failure creates no Revision and does not advance the head.

Restore Revision revalidates retained dependencies and copies the selected historical content into a new later Revision. It never moves the head backward or repoints it to an older row. `source_revision_id` records the restored source.

Duplicate revalidates the exact current Revision and atomically creates an independent custom Agent with its own v1 Revision. The new head records source Agent and Revision IDs. It never follows or merges later source changes.

Built-in Agents use the same Revision validation and dependency-binding rules. Distribution registration creates v1 or advances to another Revision only when resolved content changes. Built-ins are invocable and readable but cannot be renamed, duplicated in place, archived, or otherwise mutated by ordinary users; Duplicate creates a custom Agent.

## Metadata and Lifecycle

`name` and `description` are mutable metadata. `enabled` and `archived_at` are independent lifecycle axes:

- Disable sets `enabled=false` and blocks new invocation without cancelling accepted Runs.
- Enable revalidates the current Revision and sets `enabled=true`.
- Archive requires the Agent to be disabled and sets `archived_at`.
- Unarchive revalidates the current Revision's managed-resource bindings and clears `archived_at` without enabling the Agent. It fails if any referenced stable Skill identity has been deleted while the Agent was archived.

GET returns a strong ETag. Metadata and lifecycle mutations require exact strong `If-Match`; weak validators and `*` are rejected. These mutations never advance `version` or rewrite Revisions. Historical Revisions remain readable and can be invoked while the stable Agent is enabled and unarchived and their managed-resource bindings remain eligible.

## Subagent Composition

A parent config declares each named child edge with a stable `agent_id`, optional child-local `version`, Harness context and usage policy, and one explicit Host Environment policy. Parent Revision creation resolves an omitted version to the child's current Revision and stores one exact `child_agent_revision_id`. It rejects missing, disabled, archived, unauthorized, unretained, unexecutable, cyclic, excessively large, or Environment-incompatible graphs.

Advancing a child Agent later does not change an existing parent Revision. The parent adopts new child behavior only through another parent Revision. Workers recursively reconstruct the exact finite graph into Harness `SubagentDefinition` values. A Run Override may patch the managed child roster by stable local name; acceptance resolves and freezes the complete resulting graph before work starts.

An asynchronous hosted child receives its own Thread, Run, RunAttempts, fresh `RunBindings`, the Environment selected through its frozen child policy, the exact child Revision, and a compatible Runtime lock under [Async Subagents](34-async-subagents.md). Shared children use the spawning Run's Environment; dedicated children allocate from their exact template revision. Preparation is eager or lazy under that Environment's policy. Inline children borrow the active Harness facade.

## Run Selection and Reconstruction

A new root Run supplies an `agent_id` and may supply an exact `agent_revision_id`. Omission selects `current_revision_id`; exact selection never falls back. An optional `expected_current_revision_id` is an independent optimistic precondition rather than the selector itself.

Durable acceptance:

1. authorizes invocation of the stable Agent and every selected managed resource;
2. requires the Agent to be enabled and unarchived;
3. validates that the exact Revision belongs to the Agent, remains retained and executable, and satisfies current authorization and compatibility requirements;
4. applies the typed config override and capability overlay and resolves every final selection, including every Skill binding to an exact Revision lock;
5. freezes the complete non-secret `EffectiveAgentConfig` and exact Runtime lock;
6. when an Environment is selected, fixes the Run's Environment ID and access ceiling independently of `EffectiveAgentConfig`, and updates the Thread default; and
7. persists `agent_id`, exact `agent_revision_id`, selector kind, effective-config digest, and `runtime_lock_digest` on the accepted execution state.

Historical AgentRevisions remain invocable under the stable Agent's current lifecycle gate. Their pinned Skill selections remain exact; their unpinned selections resolve current Revisions within the `skill_id` bindings frozen in that historical AgentRevision. Account reception and Schedule definitions store the stable Agent identity and resolve the current AgentRevision for each occurrence. Retry, waiting feedback, and other successor operations preserve source Skill locks where required but still pass current Skill lifecycle gates before a new Run is accepted. Recovery and Worker replacement of an already accepted Run use its exact Revision and effective configuration.

For each outbound model request, the Worker or Runner rechecks the current Model and Model Provider lifecycle and resolves the Provider's current configuration and credential as defined by Model Management. For each execution attempt, the current `RunAttemptExecutor` in the selected Worker or Runner verifies the exact Runtime lock, records bounded compatibility identities, reauthorizes mutable authorities required by their owning contracts, constructs fresh Models, Plugins, `RunBindings`, and a ready or transparently lazy Environment operation object, and enters the Harness only after the current attempt fence authorizes effects. An accepted Run's exact Skill package locks remain internally readable even if the Skill is later deleted. Deployment code may change between attempts, but one accepted Run never silently changes its snapshotted upstream model, calling API, effective model settings, Plugin code, dependencies, other managed-resource Revisions, child graph, external tool source selections and scopes, output contract, logical Environment selection, or retry budgets. Model catalog profile and limits remain descriptive metadata under Model Management rather than frozen execution settings.

## Managed Harness Plugin Reference

`AgentConfig.plugins`, `AgentRunOverride.plugins`, `AgentRevision.plugin_runtime_mode`, `resolved_plugin_versions`, and `runtime_lock_digest` use the canonical [Managed Harness Plugins and Runtime](36-managed-harness-plugins-and-runtime.md) contract. That document exclusively owns Plugin and PluginVersion identity, selection variants, lifecycle, commands, Wheel and dependency artifacts, Runtime locks, loading, activation, and failure semantics. This document owns only where selections and immutable results are embedded in Agent configuration, Revision creation, Run overrides, and reconstruction.

## Persistence

Creator and updater attribution use IAM `ActorRef`: human and Service Account Principals retain their actual kind, while builtin reconciliation records `system` with its stable system actor ID. System attribution never grants authentication or invocation authority.

The `agents` table stores stable identity, organization ownership, name, description, `version`, `current_revision_id`, lifecycle axes, duplication provenance, actors, and timestamps. `(workspace_id, normalized_name)` is unique.

The `agent_revisions` table stores complete config, frozen resolution, digests, provenance, actor, and creation time. `(agent_id, version)` is unique. The Agent head and current Revision advance atomically. Runs and downstream records store `agent_revision_id`, not only an Agent ID or version.

## Agent Management API Contract

- `POST /api/v1/workspaces/{workspace_id}/agents`
- `GET /api/v1/workspaces/{workspace_id}/agents`
- `GET /api/v1/agents/{agent_id}`
- `PATCH /api/v1/agents/{agent_id}`
- `POST /api/v1/agents/{agent_id}/revisions`
- `GET /api/v1/agents/{agent_id}/revisions`
- `GET /api/v1/agent-revisions/{revision_id}`
- `POST /api/v1/agents/{agent_id}/revisions/{revision_id}/restore`
- `POST /api/v1/agents/{agent_id}/duplicate`
- `POST /api/v1/agents/{agent_id}/{enable|disable|archive|unarchive}`

Create, Create Revision, Restore, Duplicate, and lifecycle commands require `Idempotency-Key`. Versioned Revision creation uses `expected_version`; metadata and lifecycle mutations use strong `If-Match`. Agent and Revision collections use opaque cursor pagination, and Revision List defaults to descending `version` with a stable ID tie-breaker.

Agent actions are authorized against the stable Agent identity. Role bindings use `resource_type="agent"`. List and Get authorize `agent.read`; Create authorizes `agent.create`; metadata changes authorize `agent.update`; Create and Restore Revision authorize `agent.revision.create`; lifecycle changes authorize `agent.lifecycle`; Duplicate authorizes `agent.duplicate`; and Run acceptance authorizes `agent.invoke`. Every successful mutation records exact Agent and Revision references in security audit and outbox evidence without secret values or resolved credentials.

## Failure Semantics

| Code                            | Meaning                                                                 |
| ------------------------------- | ----------------------------------------------------------------------- |
| `agent_not_found`               | Agent is absent or concealed                                            |
| `agent_revision_not_found`      | Revision is absent, concealed, or does not belong to the required Agent |
| `agent_disabled`                | New invocation is blocked                                               |
| `agent_archived`                | The requested operation is unavailable for an archived Agent            |
| `agent_revision_not_executable` | The exact retained Revision cannot currently execute                    |
| `current_revision_conflict`     | `expected_current_revision_id` does not match the current Revision      |
| `agent_version_conflict`        | `expected_version` does not match the Agent version                     |
| `agent_revision_create_failed`  | Resolve-and-build validation failed before Revision creation commits    |
| `etag_mismatch`                 | Strong `If-Match` does not match mutable head metadata                  |

Revision-creation failure can expose only a bounded safe reason and config field path. Schema, authorization, and idempotency failures retain the common Platform API codes.

## Compatibility and Trade-offs

The canonical Service v1 resources are `Agent` and `AgentRevision`. Service exposes no parallel AgentPreset resources or aliases. Durable Run and state schemas use `agent_id` and `agent_revision_id` directly. `AgentRunOverride` is request data and `EffectiveAgentConfig` is a Run-owned snapshot; neither creates another Agent identity.

Atomically creating and advancing immutable Revisions removes a mutable draft/default-pointer lifecycle and gives `version` one canonical meaning. It requires callers to submit complete replacement configuration and use Restore to copy historical content into a new head. Finite typed overrides preserve application-specific composition without introducing arbitrary patch paths or a second managed Agent resource.

## Invariants

01. `Agent` is the only durable Agent authoring, authorization, lifecycle, and invocation identity; every Agent has one current immutable `AgentRevision`.
02. `Agent.version` always equals its current Revision version and advances only when a genuinely different Revision becomes current.
03. Metadata and lifecycle mutations use strong ETags and never advance the Agent version or rewrite Revisions.
04. Every accepted Run pins one exact AgentRevision and one immutable `EffectiveAgentConfig`; retry, waiting, recovery, and Worker replacement never remerge current Agent state.
05. Revision and effective-config content contain only serializable Service data and exact references, never Python objects, callable handlers, credential values, arbitrary import targets, or Plugin artifacts.
06. Current credentials, authorization, Provider eligibility, `RunBindings`, and Environment operation objects are resolved or constructed freshly for every execution attempt without changing the Run's accepted logical Environment. Managed target rebuilding follows the Environment generation contract.
07. Historical AgentRevision invocation never falls back to another AgentRevision; only explicitly unpinned Skill bindings and other owner-defined mutable selections resolve at new Run acceptance.
08. Restore copies retained content into a new later Revision and never moves the Agent head backward.
09. ProtocolConfig is Agent-owned Revision content rather than another resource, digest, or per-Agent protocol switch.
10. Plugin identity and lifecycle remain owned exclusively by the managed Plugin contract; Agent Management stores only typed selections and resolved locks.
