# Agent Composition and Snapshots

## Design Position

An Agent UI Agent is a reloadable local product definition composed from exact Model, Prompt, Plugin instance, Skill, Capability, and child-Agent revisions. Agent UI resolves the complete finite graph into an immutable content-addressed snapshot and reconstructs one process-local Harness `AgentDefinition` graph before a Session can use it.

An Agent definition is not a serialized Harness `AgentDefinition`. It contains no Python class, callable, native Model, Capability instance, Toolset, plugin instance, provider client, credential, Environment binding, or live Agent. Trusted Agent UI adapters map supported declarative resources to installed code under the accepted configuration generation.

Environment is deliberately outside Agent composition. An Agent declares authored Environment behavior through Capabilities and requirements, while a Session selects an independent Environment snapshot and supplies fresh provider attachments for every root and child invocation.

## Boundaries

| Concern                                                   | Owner                                     | Agent relationship                                                                       |
| --------------------------------------------------------- | ----------------------------------------- | ---------------------------------------------------------------------------------------- |
| Reloadable Agent source document and component references | Agent UI configuration catalog            | Validates and versions desired composition                                               |
| Model, Prompt, Plugin, and Skill revision content         | Owning Agent UI resource catalogs         | Referenced exactly during resolution                                                     |
| Native Agent definition and build lifecycle               | Harness                                   | Receives reconstructed native values through its public build contract                   |
| Capability behavior and namespaced state                  | Capability and Harness                    | Agent selects supported declarative forms; state remains in `HarnessState`               |
| Harness plugin factories and middleware                   | Harness plugin system                     | Agent UI constructs the exact Harness plugin document and trusted Build Context          |
| Child topology and immutable built collection             | Harness subagent contract                 | Snapshot reconstructs complete child definitions and authored edge ceilings              |
| Model-facing child execution                              | Agent UI async-subagent Capability        | Async-only tools over exact built children; fresh run attachment supplies Host authority |
| Environment desired state and provider resources          | Agent UI Session and Environment Provider | Independent Session selection; never serialized into Agent composition                   |
| Current Model, Environment, credentials, and Identity     | Fresh Host bindings                       | Reauthorized for every invocation                                                        |
| Durable hosted definitions and Presets                    | Foundation Service                        | Independent schema and revision authority; no implicit conversion                        |

## Agent Definition Document

The conceptual strict serialized form is:

```python
class AgentDefinitionDocument(BaseModel):
    schema_version: str
    agent_id: str
    display_name: str
    description: str | None
    model: ResourceRef
    prompt: ResourceRef
    plugins: tuple[ResourceRef, ...]
    skills: AgentSkillConfiguration
    capabilities: tuple[FirstPartyCapabilitySelection, ...]
    environment: AgentEnvironmentRequirements
    subagents: tuple[SubagentEdge, ...]
    async_subagents: AsyncSubagentConfiguration
    output: AgentOutputSelection
    model_recovery: ModelRecoverySelection


class AgentSkillConfiguration(BaseModel):
    available: tuple[ResourceRef, ...]
    materialization_binding: str | None
    default_selection: SkillNameSelection


class SkillNameSelection(BaseModel):
    mode: Literal["all", "exact"]
    names: tuple[str, ...] = ()


class SubagentEdge(BaseModel):
    name: str
    description: str
    agent: ResourceRef
    context: DelegationContextPolicy
    usage_limits: UsageLimits | None
    environment: ChildEnvironmentPolicy
    lifetime: Literal["parent_scope", "session"]
    steering: Literal["enabled", "disabled"]
    continuation: Literal["enabled", "disabled"]


class AgentEnvironmentRequirements(BaseModel):
    bindings: tuple[EnvironmentBindingRequirement, ...]


class EnvironmentBindingRequirement(BaseModel):
    binding_name: str
    required_operations: frozenset[str]


class ChildEnvironmentPolicy(BaseModel):
    mode: Literal[
        "none",
        "dedicated",
        "shared_root",
        "serialized_root",
    ]
    bindings: tuple[str, ...] | None


class AsyncSubagentConfiguration(BaseModel):
    tools: Literal["standard", "disabled"]
    max_active_jobs: int
    max_jobs_per_run: int
    max_depth: int
    completion_delivery: Literal["active_or_next_run", "manual"]
```

`model`, `prompt`, `plugins`, every `skills.available` entry, and child `agent` values reference resources by stable identity in one candidate configuration generation. Resolution replaces every reference with its exact `ResourceRevisionRef`; an immutable resolved snapshot never retains “latest” lookup semantics.

Plugin order is significant. Available Skill revisions form a canonical name-keyed set; source-file ordering is retained for authoring display but does not create different runtime behavior after the same unique final catalog resolves. `default_selection.mode="all"` exposes the complete conflict-resolved available catalog. `mode="exact"` exposes only the unique exact names in `names`; an empty tuple deliberately exposes no Skills. Names select final `SKILL.md` identities rather than resource IDs, source patterns, exclusions, or paths. Resolution rejects an exact name absent from the available imported revisions and rejects ambiguous duplicate final names. `materialization_binding` is absent exactly when `available` is empty; otherwise it names one required Agent Environment binding whose FileOperator operations permit the Agent UI materializer to reconcile and scan its reserved subtree.

Immediate child names are unique. A child edge selects one complete child Agent, authored context and usage ceilings, an explicit Environment resource policy, and narrower Host lifecycle controls. It does not inherit the parent's tools, Capabilities, plugins, model, Prompt, Skill selection, credentials, or live bindings. `parent_scope` requests cancellation when the spawning root Turn or parent async job is cancelled, interrupted, or abandoned; `session` permits accepted work to outlive that immediate owner until completion, explicit cancellation, Session deletion, or process shutdown. Neither policy makes active execution restart-durable. `max_active_jobs` bounds accepted, queued, and running jobs owned by the Agent node; waiting jobs retain state but consume no live-execution slot. `max_jobs_per_run` bounds new submissions from one root or child Harness Run.

`AgentEnvironmentRequirements` declares the binding names and provider-neutral operation families that must be available when a Session pairs this Agent with an Environment. A node with available Skills includes the list/stat/read/write/create/remove operations required by its `materialization_binding`; an exact empty exposure still performs full source materialization and validation because Harness exact-name selection occurs after discovery. Agent resolution validates syntax and operation keys but cannot prove resource availability because Environment selection is independent. Session creation or fork performs the cross-snapshot compatibility check against the selected Environment topology and permission ceilings.

`none` supplies an empty child Environment topology and is compatible only when the child node has no available Skills or other required Environment binding. `dedicated` creates or resumes independently fenced provider resource instances scoped to the async child job and requires every selected provider to advertise `MULTIPLE_FROM_SPEC`. `shared_root` acquires separate concurrent attachments from the root resource instances and requires every selected provider to advertise `SHARED`; the child intentionally observes and can mutate the same underlying workspace. `serialized_root` keeps the accepted job queued until selected root instances have no active attachment, then acquires fresh attachments sequentially. `bindings` selects a subset of the Session Environment topology or is absent to select all bindings. Unknown names, insufficient operations, or unsupported allocation/concurrency capabilities fail Agent/Environment compatibility during Session creation or fork, before provider effects.

`AgentOutputSelection` maps through a trusted Agent UI adapter to one native Harness business-output contract. The interactive default is text, but structured first-party output contracts can be selected by schema key and version. Arbitrary Python output classes and import targets are not serialized.

## Composition Formula

One resolved Agent is exactly:

```text
Agent revision
  = Model revision
  + Prompt revision
  + ordered Plugin instance revisions
  + ordered available Skill revisions and exact default exposure
  + curated Capability selections
  + Environment requirements
  + complete child Agent revisions, edges, and async policy
  + output and recovery policy
```

The sources have distinct responsibilities:

- **Model** selects native provider/model behavior and defaults; fresh credentials and native Model resolution remain run-scoped.
- **Prompt** supplies the complete ordered static system prompt.
- **Plugin instances** supply trusted Harness-wide middleware through the Harness-owned plugin contract.
- **Skills** supply inspectable reusable instructions and artifacts through one explicit Harness `SkillManager` composition plus fresh exact-name run selection.
- **Capabilities** own Agent-loop tools, Toolsets, guidance, settings, and hooks.
- **Environment requirements** declare provider-neutral binding/operation needs without selecting provider resources.
- **Child Agents** are complete recursively resolved Agent definitions.

A plugin can contribute Capabilities through the Harness lifecycle, but Agent UI does not add a second plugin hook model. A Skill cannot directly install Python code, a plugin, a provider, or a Capability. A Prompt cannot enable tools by naming them. Every executable code-selection surface is an installed trusted adapter, curated Capability key, Harness plugin factory key, or Environment provider key selected under Host policy.

## Model Reconstruction

The resolved Model revision contributes a logical model ID to `AgentSpec`. Agent UI validates and locks Model adapter provenance, but it does not infer credentials or construct a native Model from ambient process state. The embedding Host supplies one `RunModelResolverFactory` when it opens the application service. For every root or child Harness Run, Agent UI calls that factory with the exact pinned Agent snapshot and requires a fresh callable Harness `RunModelResolver`.

The returned resolver receives the ordinary Harness run context and logical model ID. Within that invocation scope, the Host collaborator:

1. verifies that the requested logical ID belongs to the pinned Model revision and remains allowed by current Host policy;
2. resolves current credential material behind the pinned `credential_ref`;
3. constructs or obtains the exact native Pydantic AI Model through the selected adapter;
4. applies the pinned endpoint and model settings;
5. returns the Model only under that fresh Run's authority.

Opening Agent UI without this collaborator remains valid for configuration, composition, Session, Environment, and replay operations, but foreground or child execution fails explicitly with `model_resolver_unavailable` before Harness dispatch. Agent UI never substitutes a fake Model, historic credential, or ambient provider default. A factory that returns a non-callable value fails as `model_resolver_invalid`.

The Agent snapshot locks every selected Model adapter by exact key and Agent UI distribution version. Reconstruction verifies every Model lock as well as every Harness plugin lock before building the definition graph; an allowlisted but unregistered string is never treated as an adapter. A Host Model adapter can reuse a documented reentrant native client or Model internally, but that cache is process-local and keyed by non-secret configuration plus credential generation. A Session snapshot never stores the native value or historic secret. Credential rotation behind one reference can affect a later Run without changing Agent behavior content; changing provider, endpoint, model name, settings, or credential reference creates another Model revision.

## Prompt, Skill, and Capability Reconstruction

Prompt resolution produces complete immutable system-prompt content before the Harness build and places its ordered blocks in the Harness `AgentSpec.system_prompt` field. The executable never rereads a mutable Prompt source file. Capability and Toolset instructions remain separate native Pydantic inputs with their own static or dynamic lifecycle.

Each available Skill revision was imported through the public Harness `SkillManager` and Agent UI's Host-owned revision process. Reconstruction verifies its immutable package object, manifest, and digest. For each Agent node, Agent UI constructs one explicit `SkillManager` whose ordered source roots contain only that node's pinned package revisions and whose trusted materializer writes those exact files through the current run Environment's `FileOperator` into a content-addressed Agent UI-owned logical root. The root is unique to the Agent snapshot and node and resolves beneath `materialization_binding`; materialization verifies or replaces that exact managed subtree before scanning, so stale files from another revision cannot enter the catalog. Session compatibility rejects missing or insufficient file operations before provider effects. Passing this manager to `SkillsCapability` replaces the Harness default workspace source; no ambient home, project, package, or sibling directory enters the runtime catalog.

Run preparation uses the Harness dependency boundary directly. `SkillManager.scan_environment()` captures every configured root, opens exact revision-pinned `FileOperator` scopes, performs materialization and discovery, validates resolved documents and conflict/catalog bounds, and reselects every configured root before returning a `BoundSkillCatalog`; this scan-time fence includes empty roots and roots whose items lost conflict resolution. `SkillsCapability` then applies the fresh exact-name run selection and uses `BoundSkillCatalog.require_current()` to fence only the selected catalog-bearing logical routes before each model request and before and after each tool execution. A topology change unrelated to those routes does not invalidate the selected catalog. The Capability also owns `SkillPath` publication, instructions, and access events. The process-local bound catalog is neither persisted nor reused by another logical Run. The manager uses `conflict="error"`, and its catalog bounds are locked in the snapshot. Because Agent UI-managed available revisions must have unique final names, ambiguity fails snapshot resolution rather than being silently hidden by source precedence.

When an Agent node has no available Skills, Agent UI omits `SkillsCapability`; only an empty effective selection is valid. Otherwise, for every root or child Harness Run, Agent UI derives the pinned effective selection and supplies a fresh `SkillSelectionRunCapability(names=frozenset(...))` when the selection is exact. Omitting that run Capability means the complete conflict-resolved available catalog; supplying an empty set means no Skill instructions or paths; an unknown exact name fails preparation before model exposure. The selection is repeated on resumed Runs and is never restored from `HarnessState`. A child derives its own Agent node and Session selection; the parent's allowlist is not inherited.

Skill content is immutable for the executable snapshot. Materialization or selection grants no filesystem operation, credential, plugin, Capability, or package authority. `skills_catalog_resolved` and `skill_accessed` Harness context events remain observations and enter Agent UI's ordinary AG-UI processing path.

`FirstPartyCapabilitySelection` is a discriminated union owned by the Agent UI schema. Each member has an exact key, schema version, and typed configuration. Unknown keys or versions fail resolution. Agent UI adapters construct public Harness/Pydantic Capability values; there is no arbitrary Capability import registry.

The curated catalog includes the dynamic Environment, Working State, user interaction, document/media/web, Session-read, and Agent UI async-subagent behavior supported by the selected Agent UI release. A Capability that requires Host collaboration follows the two-layer pattern:

- the definition-selected Capability owns stable model-visible behavior;
- a fresh run Capability supplies current Session, Environment, repository, or async-subagent service authority.

Missing or mismatched fresh collaboration fails the affected operation before side effects.

## Plugin Reconstruction

Agent resolution uses the public Harness plugin factory catalog to construct the ordered Plugin instances selected by each resolved Agent node. For every selected Plugin resource, Agent UI supplies its exact `plugin_key`, `plugin_id`, normalized configuration, and Host-owned bounded extensions to `HarnessPluginFactoryCatalog.create_plugin()`, then places the resulting trusted concrete object in that node's `AgentDefinition.plugins` tuple. The ordinary Harness build path owns ordering, Agent binding, run binding, Capability contribution, middleware, result validation, and cleanup.

The resolved snapshot locks:

- plugin resource revision;
- `plugin_key` and `plugin_id`;
- normalized credential-free configuration;
- selected distribution name/version and factory provenance;
- Harness plugin factory/configuration contract version.

At reconstruction, Agent UI verifies current policy and provenance and builds a fresh immutable factory catalog containing only the selected keys. An unavailable or changed locked plugin fails explicitly; it is never replaced with the latest similarly named factory. Root and child Agents can select different Plugin resources because concrete direct Plugin tuples belong to their complete definitions. One recursive `HarnessBuilder` still constructs the finite graph; Agent UI never mutates the builder or an executable after construction.

Agent UI does not use ambient Harness plugin files or environment switches for product Agent composition. A process-wide operator Plugin document, when explicitly supported by process settings, remains a separate builder-wide layer governed by the Harness configuration contract and participates in the executable digest; it cannot replace per-Agent Plugin resources or be changed in an active executable.

## Resolution and Snapshot

```mermaid
flowchart LR
    Generation[Accepted configuration generation]
    Root[Selected Agent revision]
    Resolve[Resolve exact component and child revisions]
    Validate[Validate locks, graph, Capabilities, and policy]
    Snapshot[Immutable resolved Agent snapshot]
    Store[Compressed content-addressed object]
    Reconstruct[Trusted native reconstruction]
    Definition[Complete AgentDefinition graph]
    Build[HarnessBuilder]
    Executable[ExecutableAgent]

    Generation --> Root --> Resolve --> Validate --> Snapshot --> Store
    Snapshot --> Reconstruct --> Definition --> Build --> Executable
```

Resolution occurs before Session creation, explicit fork, or validation preview. It:

1. captures one accepted configuration generation;
2. resolves the root Agent and every Model, Prompt, Plugin, Skill, Capability, output, and child reference;
3. expands the finite child graph and rejects missing revisions, duplicate immediate-child names, and cycles;
4. validates Capability combinations, Environment requirements, exact Skill names, async-subagent policy, and child edge ceilings;
5. verifies selected adapter and package provenance under current policy;
6. canonicalizes all behavior-affecting content and dependency locks;
7. computes a logical Agent digest;
8. publishes the immutable resolved snapshot before a Session can reference it.

The conceptual snapshot is:

```python
class ResolvedAgentSnapshot(BaseModel):
    snapshot_schema_version: str
    root_agent: ResourceRevisionRef
    logical_agent_digest: str
    resolved_agents: tuple[ResolvedAgentNode, ...]
    models: tuple[ResolvedModelRevision, ...]
    prompts: tuple[ResolvedPromptRevision, ...]
    plugins: tuple[ResolvedPluginRevision, ...]
    skills: tuple[ResolvedSkillRevision, ...]
    skill_packages: tuple[SkillPackageObjectRef, ...]
    skill_selections: tuple[ResolvedAgentSkillSelection, ...]
    capability_schemas: tuple[CapabilitySchemaLock, ...]
    adapter_locks: tuple[DependencyLock, ...]
    harness_release: str
```

The snapshot contains every authority-neutral value needed to repeat trusted reconstruction. It contains no secret, native Model, current credential, Environment specification or resource state, plugin object, live Skill materialization, repository, task, client, or run Capability.

## Session Pinning and Executable Lifetime

A Session pins one exact resolved Agent snapshot identity and digest. A dynamic configuration reload can publish another Agent revision and executable, but it does not alter the Session. Applying another Agent composition to existing history requires an explicit [Session fork](04-sessions-environments-and-state.md#forking-and-composition-change).

An Agent graph with no managed Skills produces an `ExecutableAgent` that corresponds only to the resolved Agent snapshot and complete child graph. Agent UI caches it by logical Agent digest, Harness release, locked adapter provenance, and activated restart-bound process configuration. Supplying an Environment for compatibility validation does not create another executable identity when no definition-time component depends on that Environment.

A graph with managed Skills produces an executable for one compatibility-validated immutable Agent/Environment snapshot pair. Agent UI's authored `materialization_binding` selects an Environment binding whose resolved `model_alias` is embedded in each explicit `FileSkillSource` root at definition reconstruction, while the actual `FileOperator`, topology revision, and attachment remain fresh Run values. The pair cache key therefore includes both logical digests. This pairing is an Agent UI authoring and materialization-path contract, not a Harness-wide requirement that all definitions bind an Environment at build time. A cache entry contains no Session state, credential, Environment resource, attachment, or run authority.

Changing any behavior-affecting component creates another logical digest and executable. Changing the Environment digest creates another managed-Skill executable pair, even if the selected alias remains textually equal, so reconstruction never reuses a definition across an unvalidated pair. Agent UI never hot-toggles the system prompt, instructions, plugins, available Skills, default Skill exposure, Capabilities, output, recovery policy, async policy, or child edges inside an active executable. A Session can pin an exact root/child Skill exposure override at creation or fork, but changing that pinned override also requires a fork. Closing the last cache reference closes the complete built child and plugin graph through the ordinary Harness ownership order.

Run-time values vary only through contracts designed for fresh binding: Identity, current model resolver, credentials, exact Skill selection, Environment attachments, policy narrowing, Session-read collaborator, and async-subagent collaborator. Fresh binding realizes the Session-pinned effective Skill selection and can apply current policy narrowing, but it cannot add a Capability, plugin, available Skill, output type, or child edge absent from the snapshot or select a Skill outside that Session policy.

## Child Definitions and Async-Only Presentation

Every child edge resolves to one complete [Harness `SubagentDefinition`](../agent-harness/11-delegation-and-subagents.md#child-definitions-and-built-collection). The child owns its Model, Prompt, Plugins, available Skills and default exposure, output, Capabilities, recovery policy, nested children, and Environment requirement. One recursive Harness build produces the immutable `SubagentCollection` of exact `BuiltSubagent` values.

A `self` authoring convenience can be exposed by the configuration editor, but resolution expands it to a finite complete child revision and narrows recursive child topology before snapshot publication. Runtime cloning of the parent Agent, context, credentials, Skill selection, or authority is not supported.

Agent UI never installs the Harness first-party `DelegationCapability` or exposes its blocking inline `delegate` path. `async_subagents.tools="standard"` instead selects the Agent UI-owned declarative async Capability. During each run that Capability reads the exact immediate collection from `AgentContext.subagents`; a fresh typed Agent UI run Capability supplies current Session-scoped submission, binding, scheduling, and delivery authority. `tools="disabled"` retains the built collection for trusted composition but exposes no model-facing child operation.

The standard async surface is fixed to background submission and has no execution-mode argument. It provides `delegate`, `resume_subagent`, `subagent_info`, `wait_subagent`, `steer_subagent`, and `cancel_subagent` over Host-owned job records. A bounded wait may await an independently scheduled job, but spawn never executes a child inline and parent cancellation never converts a child into Harness Delegation State. Complete runtime semantics are owned by [Runtime, Subagents, and Surfaces](05-runtime-subagents-and-surfaces.md#async-subagent-capability).

Nested children use the same rule recursively. Each async job invokes the selected child through its ordinary `ExecutableAgent.stream()` with fresh bindings, owns separate child `HarnessState`, usage, events, and delivery, and never merges child continuation into the parent's `HarnessState`.

## Configuration Reload

A newly accepted configuration generation can add, remove, or change source definitions. Resolution caches are generation-aware, while immutable snapshot and executable caches are digest-aware:

- an unchanged Agent digest can reuse an Agent-only executable when the graph has no managed Skills;
- unchanged Agent and Environment digests can reuse a managed-Skill pair executable;
- a changed required digest creates another snapshot or pair executable;
- removed current source content does not delete a snapshot pinned by a retained Session;
- an active Run and its async-subagent jobs retain the snapshot with which they started;
- package refresh can make a new adapter available but cannot unload or replace code already captured by an executable.

Validation preview can resolve and build a candidate Agent without creating a Session, but it uses ordinary reconstruction and cleanup rather than a second approximate validator.

## Failure Semantics

| Failure                                                                             | Outcome                                                                                        |
| ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Missing or wrong-kind component reference                                           | Candidate generation or explicit resolution fails; no latest substitution                      |
| Child graph cycle or duplicate immediate name                                       | Resolution fails before snapshot publication                                                   |
| Invalid Capability combination or output selection                                  | Resolution fails before Harness build                                                          |
| Unavailable locked plugin, model adapter, Skill package codec, or Capability schema | Reconstruction fails explicitly; retained snapshot remains unchanged                           |
| Exact Skill selection names an unavailable or ambiguous name                        | Resolution or run preparation fails before model exposure                                      |
| Current Host policy denies locked composition                                       | Executable creation denied; snapshot is not rewritten                                          |
| Plugin or Capability build failure                                                  | Harness build fails and closes already acquired resources                                      |
| Credential missing during a Run                                                     | Model resolution or affected operation fails; Agent snapshot remains valid                     |
| Environment requirement unsatisfied                                                 | Session creation or Run binding fails before Harness dispatch, according to requirement timing |
| Configuration changes during resolution                                             | Resolver finishes against its captured generation or restarts; it never mixes generations      |
| Configuration changes during an active Session                                      | Existing Session and executable continue with pinned snapshot                                  |

## Compatibility

Agent source schema, component resource schemas, snapshot schema, normalization rules, adapter lock format, Harness release, Plugin document version, Capability schemas, Skill contract, and `HarnessState` versions evolve independently. A migration creates a new revision or a verifiably equivalent normalized representation; it never changes content addressed by an existing digest.

A Session can continue only when Agent UI can decode its snapshot, verify its locks, reconstruct the exact logical Agent definition, and import its `HarnessState` under the owning Harness contracts. Name similarity and readable message history cannot authorize substitution of another child, Plugin, Capability, or state codec.

## Trade-offs

### Explicit component resources

Separate Model, Prompt, Plugin, and Skill resources allow reuse, independent validation, UI management, and agent-readable configuration. Resolution must maintain exact cross-resource revisions, but Agent behavior is reviewable without serializing native Python objects.

### Immutable snapshots and live catalogs

Snapshots keep Session continuation deterministic while the file-backed catalog reloads dynamically. Applying new behavior to old history requires an explicit fork rather than implicit mutation.

### Complete child Agents

Complete children repeat some authoring values but match the Harness graph and keep every behavior and authority edge explicit. Authoring conveniences disappear during resolution rather than becoming runtime inheritance.

## Invariants

01. Agent composition is the exact combination of pinned Model, Prompt, Plugin, available Skill, default Skill exposure, Capability, output, async policy, and child-Agent revisions.
02. Environment desired state and provider resources remain outside Agent composition and are selected by a Session.
03. Every Session pins one immutable resolved Agent snapshot and logical digest.
04. Dynamic reload, Plugin toggles, and Prompt/Skill edits never mutate an active executable or existing Session.
05. Every child edge resolves to one complete finite child definition; no separate subagent builder or runtime inheritance plane exists.
06. Agent UI exposes only its Host-owned async-subagent Capability; it never enables the Harness blocking inline Delegation Capability.
07. Every root and child Run receives a fresh exact-name Skill selection derived independently from its pinned Agent node and Session policy.
08. Curated Capability keys, trusted model adapters, enabled Harness plugin keys, and selected Environment providers are the only declarative code-selection surfaces; arbitrary imports are rejected.
09. Snapshots restore no Identity, credential, Environment, provider client, repository, scheduler, plugin object, or execution authority.
10. Every root and child invocation receives fresh model, Environment, policy, Skill-selection, and Host collaboration bindings.
11. Applying another Agent composition or pinned Session Skill exposure to existing history requires an explicit Session fork.
12. Reconstruction uses the public Harness build contract and never implements a second Agent loop.
