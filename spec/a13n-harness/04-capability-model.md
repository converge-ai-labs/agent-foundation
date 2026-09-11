# Capability and Agent Context Model

## Design Position

Reusable behavior inside the Pydantic Agent loop uses native `AbstractCapability[AgentContext]`. Capability is the only top-level feature-behavior composition plane in `AgentDefinition`: each feature Capability owns configuration, definition/run binding, lifecycle, feature-level instructions, hooks, native ordering, and Toolset composition as one coherent unit. Its owned Toolset owns model-visible tool schemas, guidance for using those tools, complete per-call orchestration, and model-safe result or error projection. Tool guidance is contributed only with the Toolset and the exact tools it describes; a Capability does not publish unconditional guidance for an optional or superseded tool surface.

A Capability may be constructed with a narrow trusted operator interface when canonical work must remain Host-owned or outlive one Run. The Capability still owns mode selection, Toolset composition, compact references, state projection, callback interpretation, and tool results; the operator owns only its documented execution boundary and never supplies tools or guidance. A stable operator receives explicit current-run correlation on every call and must not derive authority from mutable Capability state. The Harness does not define a second Capability base, lifecycle, ordering graph, or generic job interface. Fresh run attachment Capabilities enter `RunBindings` only for behavior whose owning contract explicitly requires per-run attachment.

Environment itself is not a Capability. Harness enters fresh adapters supplied as Run inputs and exposes only their provider-neutral bound facade through the fixed `AgentContext.environment` field. The optional `DynamicEnvironmentCapability` consumes that field to compose file and shell Toolsets, contribute dynamic context and notices, and own their feature lifecycle; the composed Toolsets own their respective stable tool guidance. The independent optional `ShellReviewCapability` can classify marked shell command launches at the managed invocation boundary without becoming part of Environment configuration or provider behavior. Capability presence cannot select a Provider, persist desired mounts, publish state, or close or destroy an Environment adapter.

Harness plugins govern only the outer semantic-input-to-complete-result boundary and may contribute ordinary Pydantic Capabilities. A plugin that needs dynamic request context contributes the explicit `AbstractModelContextCapability`; it receives no peer plugin-only context hook.

## Native Composition

| Pydantic primitive                 | Harness use                                                 |
| ---------------------------------- | ----------------------------------------------------------- |
| `AbstractCapability`               | Agent-loop behavior, model projection, and Host integration |
| `CapabilityOrdering`               | Dependencies, order, and wrapper nesting                    |
| `AbstractToolset`/`WrapperToolset` | Tool contribution and managed invocation                    |
| `RunContext[AgentContext]`         | Messages, usage, limits, tools, run-bound peers, and deps   |
| Agent/run Capability binding       | Native reentrant and fresh invocation composition           |

Pydantic AI owns `for_agent()`, `for_run()`, Toolset composition, lifecycle hooks, node hooks, and cleanup. Capability authors do not inspect private Agent graph state. A direct function tool is authored inside native `Capability(tools=[...])`; an external or custom Toolset is composed by a native Toolset Capability or another feature Capability. For a reusable Harness feature, the Toolset directly owns the callable schema and per-call behavior over narrow run-bound ports, while its Capability resolves those ports and owns Agent-loop lifecycle. A feature may contribute a provider-native tool and a local function fallback through the same Capability; the effective Model profile and Pydantic's native fallback semantics select exactly the usable surface rather than a Harness-maintained provider matrix. [Context and Memory](09-context-and-memory.md#media-documents-and-web-resources) owns the Web search instance of this contract. `AgentDefinition` and `HarnessBuilder.build()` expose no peer `tools` or `toolsets` parameters.

Pydantic's finalized Capability map and ToolManager remain authoritative. Harness-visible explicit Capability IDs are stable non-blank strings without `:`; they support uniqueness, lookup, and source provenance only. `CapabilityOrdering.wraps` and `wrapped_by` use concrete Capability types or instances, and `requires` uses concrete types; IDs are not ordering references, and the Harness adds no second Capability sorter. The same finalized order governs the narrow model-context middleware subtype described in [Context and Memory](09-context-and-memory.md#model-context-projection-contract); `ModelContextCoordinatorCapability` is the sole mandatory infrastructure owner of message placement and does not create another ordering graph.

## Capability Sources and Authorization

Capability availability and Capability grant are distinct. The Harness assigns every Capability to one construction source and rejects a type or reserved ID from any source not explicitly allowed:

| Source                                     | Accepted value                                                                                           | Authority boundary                                           |
| ------------------------------------------ | -------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| Native `AgentSpec.capabilities`            | Native built-ins, first-party Harness serializable feature types, and exact Host-authorized custom types | Definition behavior only; no fresh Host authority            |
| `AgentDefinition.capabilities`             | Concrete `AbstractCapability[AgentContext]` feature instances                                            | Trusted process-local definition behavior                    |
| `AbstractHarnessPlugin.get_capabilities()` | Concrete plugin-owned feature or infrastructure instances                                                | Plugin contribution only                                     |
| Mandatory Harness infrastructure           | Exact Harness-created concrete types                                                                     | Framework authority; never declarative or caller-replaceable |
| `RunBindings.capabilities`                 | Exact documented concrete fresh attachment/policy types                                                  | Current-run authority under reserved types and IDs           |

A type is denied from every unlisted source. Mandatory infrastructure and run-only authority types never enter the declarative custom-type catalog. A definition or plugin instance cannot use `for_run()` to replace itself with a run-only reserved type or ID, and a run attachment cannot launder itself into definition or mandatory infrastructure.

Bare Pydantic `CapabilityFunc` values are not accepted in `AgentDefinition`, plugin contributions, or `RunBindings`. Pydantic resolves such a function once per native Agent run, while one logical Harness run can contain several native recovery attempts. Support requires a future Harness-bound form that resolves once per logical run, validates the complete result tree, memoizes it, and reuses the exact result across every attempt. Concrete `AbstractCapability` instances are the current contract.

## Declarative Custom Capability Types

`HarnessBuilder` can receive one exact immutable `CapabilityTypeCatalog` constructed by trusted Host code. The catalog is narrow AgentSpec reconstruction input, not a general class registry or package-discovery service:

```python
@dataclass(frozen=True, slots=True)
class CapabilityTypeRegistration:
    serialization_name: str
    capability_type: type[AbstractCapability[AgentContext]]


@dataclass(frozen=True, slots=True)
class CapabilityTypeCatalog(
    Mapping[str, CapabilityTypeRegistration]
):
    registrations: tuple[CapabilityTypeRegistration, ...] = ()

    @property
    def custom_capability_types(
        self,
    ) -> tuple[type[AbstractCapability[AgentContext]], ...]: ...

    @classmethod
    def from_types(
        cls,
        capability_types: Sequence[
            type[AbstractCapability[AgentContext]]
        ],
    ) -> CapabilityTypeCatalog: ...
```

Every registered class is a direct dataclass-declared `AbstractCapability`, has a non-blank stable serialization name, does not collide with native or Harness names, is authorized for the `AgentSpec` source, and can participate in deterministic native schema construction. The Host owns package discovery, installation trust, artifact locks, and catalog population. Two builders can use different immutable catalogs in one process without global mutation.

Before `Agent.from_spec()`, the Harness validates every visible `CapabilitySpec` name and nested capability-valued spec against the native registry, the closed first-party Harness declarative set, and the exact Host catalog. The closed first-party declarative set currently includes `ShellReviewCapability`; it is available without Host catalog registration but remains disabled unless the `AgentSpec` explicitly selects it. After construction, the Harness traverses the complete instantiated Capability tree and verifies type/source permission, stable IDs, singleton constraints, and reserved infrastructure provenance before publishing the executable. At each native run boundary it revalidates the finalized Capability mapping so `for_run()` replacement cannot change a protected type, ID, or source. Custom type availability alone grants no Capability; the `AgentSpec` must explicitly select it.

## AgentContext

Two passive process-local publications let independently authored Capabilities describe run facts without introducing another lifecycle or lookup plane:

```python
@dataclass(frozen=True, slots=True)
class SkillPath:
    name: str
    source_id: str
    directory: EnvironmentPath


@dataclass(frozen=True, slots=True)
class ToolMetadataKey[T]:
    name: str
    value_type: type[T]


class RunSkillPaths:
    def publish(
        self,
        owner_id: str,
        paths: Sequence[SkillPath],
    ) -> None: ...

    @property
    def values(self) -> tuple[SkillPath, ...]: ...


class ToolRuntimeMetadata:
    def publish[T](
        self,
        key: ToolMetadataKey[T],
        owner_id: str,
        value: T,
    ) -> None: ...

    def values[T](
        self,
        key: ToolMetadataKey[T],
    ) -> tuple[T, ...]: ...
```

A built-in or external Capability can publish resolved skill paths. Any Toolset package can define and export a typed metadata key and immutable value class, and any compatible Capability can publish values under that key. Publications are owner-bound and idempotent: repeating the same owner and value is valid, while changing that owner's value or supplying the wrong runtime type fails deterministically. Snapshots are immutable tuples; published values remain process-local objects and are required by contract to be passive values rather than mutable services. There is no central registration of permitted external key types or namespaces.

```python
@dataclass(frozen=True, slots=True)
class AgentContext:
    run_id: str
    instance: AgentInstanceContext
    state: AgentContextState
    environment: Environment
    model_resolver: RunModelResolver | None
    toolset_instructions: bool
    events: HarnessEventEmitter
    usage_attribution: RunUsageLedger
    plugins: BoundPluginContext
    subagents: SubagentCollection
    metadata: Mapping[str, JsonValue]
    model_context: ModelContextMiddleware | None
    skill_paths: RunSkillPaths
    tool_metadata: ToolRuntimeMetadata

    @property
    def identity(self) -> AgentIdentityRef: ...

    async def record_provider_usage(
        self,
        usage: ProviderUsage,
        *,
        source: str,
        tool_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> ProviderUsageRecord: ...

    async def project_model_context(
        self,
        request: ModelContextProjectionRequest,
    ) -> ModelContextProjection: ...

    async def export_state(
        self,
        message_history: Sequence[ModelMessage],
    ) -> HarnessState: ...
```

One fresh context is created for every logical Harness run and reused by that run's internal `ModelAttempt` values. Fields have cohesive cross-feature meaning:

- `instance` is the trusted workload, actor, and lineage binding;
- `state` coordinates detached Capability namespaces;
- `environment` is the entered Harness lifecycle facade, independent of Capability composition;
- `model_resolver` is the optional fresh logical-model resolver;
- `toolset_instructions` is the effective per-run switch for Toolset-owned model guidance;
- `events` emits bounded Harness-owned observations into the one canonical run stream;
- `usage_attribution` retains mixed-source immutable records and reports them at model-request boundaries;
- `plugins` indexes the complete fresh run-bound plugin graph after binding;
- `subagents` is the immutable collection owned by the executable;
- `metadata` is immutable non-authoritative correlation;
- `model_context` is the optional fresh Host wrapper around this run's projection chain;
- `skill_paths` is the shared passive snapshot of explicitly selected, resolved skill directories and provenance;
- `tool_metadata` stores typed passive values whose meaning and hard-limit validation belong to the Toolset that defines each key.

`project_model_context()` is the terminal dynamic-context projection. It combines `BoundEnvironment.project_model_context()` with bounded Agent run and conversation context according to the classified input or tool-results request. It returns typed blocks only and does not edit messages, expose opaque Capability namespaces, persist rendered text, or replace Capability-owned projections such as current time, request usage, selected Host metadata, working tasks, and notes.

`identity` is derived from `instance`; no second value can diverge. The context is not a generic service locator and cannot be supplied by plugins or model content. Skill paths and tool metadata contain no callable service, lifecycle hook, ordering edge, dispatch route, authority, or durable state. They are created once with the logical-run context and reused across its internal `ModelAttempt` values.

## MCP Context Headers

For compact local MCP discovery, code-first `ToolProxyGroup` composes around the run-bound Capability rather than a definition-time Toolset. It preserves this header lifecycle and requires local-only MCP execution. [Grouped ToolProxy Discovery](07-tool-execution.md#grouped-toolproxy-discovery) owns its discovery and call contract.

`ContextualMCP` resolves outbound headers during Pydantic Capability run binding, before the returned fresh upstream `MCP` exposes either a provider-native `MCPServerTool` or a local `MCPToolset`. A code-first caller supplies any trusted sync or async `MCPHeadersFactory`. `MCPContextHeaders` is the shared declarative implementation backed by `MCPContextHeadersConfig` and exact header bindings.

The declarative resolver supports only these source namespaces:

| Source                                | Value                                               |
| ------------------------------------- | --------------------------------------------------- |
| `identity.issuer`, `identity.subject` | Fixed workload principal fields                     |
| `identity.<claim>`                    | One exact immutable Agent Identity claim            |
| `instance.agent_instance_id`          | Current Host-owned Agent instance                   |
| `instance.parent_agent_instance_id`   | Optional parent lineage                             |
| `instance.delegation_id`              | Optional delegation correlation                     |
| `instance.actor`                      | Optional actor string                               |
| `context.run_id`                      | Current logical Harness run                         |
| `context.thread_id`                   | Current independently advancing Thread              |
| `context.metadata.<key>`              | One exact top-level key from immutable run metadata |

The text following `context.metadata.` is one exact top-level key; the resolver does not reflect over arbitrary `AgentContext` objects or interpret nested attribute paths. A selected string is used directly. Any selected JSON number, boolean, object, or array is encoded with the Harness canonical compact JSON encoder; `None` and absent optional fields are missing values. A required missing value fails during Capability run binding, while an optional missing value omits that header. This permits a Host to place one deliberate dictionary or list in run metadata and send its canonical JSON string without a template or second encoding language.

Static and resolved header names are compared case-insensitively, and a duplicate fails rather than assigning implicit precedence. `ContextualMCP` otherwise delegates header and transport validation to upstream MCP and its HTTP/provider integrations. Header resolution never forwards all claims or metadata by wildcard, changes Identity, persists data, or makes a selected metadata value authoritative.

## Lifecycle Integration

| Need                                  | Integration                                            |
| ------------------------------------- | ------------------------------------------------------ |
| Produce input after Environment entry | `RunInputFactory`                                      |
| Transform semantic input/result       | Harness plugin `wrap_run()`                            |
| Bind a fresh Agent-loop feature       | Capability `for_run()`                                 |
| Resolve run-scoped MCP headers        | `ContextualMCP` and an `MCPHeadersFactory`             |
| Contribute feature instructions       | Native Capability                                      |
| Contribute tools and their guidance   | Owning Toolset                                         |
| Augment dynamic model context         | `AbstractModelContextCapability`                       |
| Override one run's context projection | Fresh `ModelContextMiddleware`                         |
| Publish passive run facts for tools   | `skill_paths` or an owner-defined `ToolMetadataKey[T]` |
| Observe model, node, or tool behavior | Native hooks plus `AgentContext.events`                |
| Attribute provider usage              | `AgentContext.record_provider_usage()`                 |
| Resolve a string Model                | Fresh resolver, then Harness inference                 |
| Store Capability continuation data    | `AgentContextState` namespace                          |
| Operate on or observe Environment     | Fixed `AgentContext.environment` resource              |
| Persist a checkpoint candidate        | Host adapter using exported `HarnessState`             |

A Capability that needs another run-bound Capability uses Pydantic's public run-bound mapping after binding. A Capability contributed by a Harness plugin resolves the matching fresh plugin through `ctx.deps.plugins.require(id, ExpectedType)`. A Capability that only needs to publish passive information for a Toolset uses the Toolset owner's public typed key instead of requiring or impersonating that Toolset's owning Capability.

The Harness does not validate class-free Host role names or maintain another registry of final Capability replacements. A Host that needs an exact run collaborator constructs a typed Capability and its feature-specific code validates the expected public type and ID before use.

## Capability State

```python
class CapabilityState(BaseModel):
    version: str
    data: JsonValue


class AgentContextState:
    async def read[T: BaseModel](
        self,
        capability_id: str,
        state_type: type[T],
        *,
        version: str,
    ) -> T | None: ...

    async def write(
        self,
        capability_id: str,
        value: BaseModel,
        *,
        version: str,
    ) -> None: ...

    async def snapshot(
        self,
    ) -> AgentContextStateSnapshot: ...
```

The owning Capability chooses a stable non-blank ID, state model, and exact version. `read()` validates those values and returns a detached typed model. `write()` atomically replaces the namespace. `snapshot()` copies every namespace.

The coordinator intentionally has no active-Capability registry. Imported entries need not be consumed before model work, and unknown namespaces remain opaque. A Capability accepts its state only by performing the typed read it requires before dependent behavior.

Trusted Python can intentionally read, replace, migrate, or transfer complete state. Namespace ownership is a composition contract, not a sandbox or cryptographic provenance mechanism.

Pydantic messages and portable Environment backend state live in separate `HarnessState` fields. Dynamic Environment stores no process namespace: process references, handles, output offsets, status, watchers, and readiness are private to one Run and expire during cleanup. Async Subagent Capability likewise stores no parent projection: current async execution references, status, output, and child state are queried through `SubagentOperator`. Inline Subagent Capability separately stores complete nested continuation state under its own namespace. Capability state contains no live handle, task, callback, provider cursor, output reference, attachment, readiness fact, operation authority, desired mount definition, Environment provider lifecycle, usage accumulator, client, credential, policy decision, queue, lock, canonical Host execution state, plugin object, or provider session. A Capability cannot obtain lifecycle authority by copying an operator selector or observation into its namespace.

## State Export

`AgentContext.export_state()` creates a detached `HarnessState` from the supplied complete message view, current Capability snapshot, and portable Environment export. It performs no persistence I/O and does not consult a Capability codec registry; Environment collection is owned by the fixed core resource rather than a Capability namespace.

The normal inner run exports aligned messages and state. Trusted result middleware may return a different well-formed state for handoff, migration, or caching. The Harness does not require equality with `HarnessRunResult.all_messages()`.

## Categories

The categories describe ownership, not subclasses:

| Category             | Examples                                                    |
| -------------------- | ----------------------------------------------------------- |
| Agent feature        | Guidance, compaction, memory, working state                 |
| Provider integration | `DynamicEnvironmentCapability`, model behavior, MCP, skills |
| Host integration     | Policy, credentials, checkpoint observation, telemetry      |
| Tool behavior        | Managed invocation, external tools, discovery               |

Each feature retains its own narrow collaborators and security checks. The Harness does not collect them into a generic map.

## Checkpoint Integration

A Capability may observe a public complete Pydantic boundary and call `AgentContext.export_state()`, then hand the candidate to a typed Host store. The store decides durability, generation, fencing, retention, and failure policy.

The Harness state API itself does not define `CheckpointStore`, choose a latest checkpoint, or load state implicitly. A Host loads one selected `HarnessState` before creating the next run.

## Failure Semantics

| Failure                                                    | Outcome                                                   |
| ---------------------------------------------------------- | --------------------------------------------------------- |
| Capability composition/order failure                       | Pydantic Agent build or run binding fails                 |
| Unknown, colliding, or source-denied declarative type      | Definition build fails before model work                  |
| Bare `CapabilityFunc` or source-laundered replacement      | Definition build or run setup fails before model exposure |
| Blank namespace ID or version                              | `StateError`                                              |
| Version mismatch on typed read                             | `capability_state_version_unsupported`                    |
| Payload fails owning model validation                      | `capability_state_invalid`                                |
| Capability hook or Toolset fails                           | Native Pydantic/Harness failure handling applies          |
| Context middleware returns an invalid or oversized overlay | Request fails before model dispatch                       |

## Boundaries

| Concern                                         | Owner                                |
| ----------------------------------------------- | ------------------------------------ |
| Capability lifecycle and Toolsets               | Pydantic AI                          |
| Context middleware ordering                     | Finalized native Capability mapping  |
| Dynamic-context placement and limits            | Mandatory Harness coordinator        |
| Shared context and Capability-state coordinator | Harness                              |
| Environment lifecycle and portable aggregate    | Harness Environment core             |
| One feature's state and behavior                | Owning Capability package            |
| Plugin middleware                               | [Plugin System](05-plugin-system.md) |
| Durable checkpoint authority                    | Host                                 |

## Trade-offs

### Capability-only Composition vs. Peer Tool Fields

One feature owner keeps tools, Toolsets, instructions, settings, hooks, and availability coherent and lets Pydantic own their native lifecycle. Authors wrap a standalone tool or Toolset in a small native Capability instead of granting it through a second top-level plane.

### Cohesive Context vs. Generic Dependency Container

A small fixed context makes Identity, Environment, model resolver, plugins, children, and state explicit. Feature-specific clients remain typed Capability or tool collaborators instead of undocumented context entries.

### Opaque State Preservation vs. Global Ownership Checks

Opaque namespaces allow independent Capability evolution and trusted state handoff. The Harness cannot assert that every stored entry was accepted by the current composition; only the owning typed read establishes that.
