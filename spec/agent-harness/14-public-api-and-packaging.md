# Public API and Packaging

## Design Position

`agent-harness` is distributed as `converge-agent-harness`. Its public API is async for execution and cleanup, while Agent construction is synchronous and code-first. It exposes native Pydantic AI types where upstream already owns the semantics and adds only the process-local definition, context, plugin, state, model-recovery, event, and result boundaries shared by embedded and hosted callers.

The package does not expose a serialized Agent-definition language, compiler, or universal extension framework. Hosted systems reconstruct trusted direct Python inputs through their own adapters and call the same public builder as embedded applications. The Harness plugin boundary additionally owns one narrow versioned preferred YAML or supported JSON document and Build Context that can select factory classes during opted-in builder construction. Environment provider specifications, discovery, Managers, resource state, and attachments belong to `converge-agent-environment-provider`; the Harness owns attachment-to-binding adaptation and its separate aggregate run-extension discovery surface.

## Root Public Surface

The package root exports these contract groups:

| Group                 | Public values                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Definition and build  | `AgentSpec`, `ModelConfiguration`, `AgentDefinition`, `HarnessBuilder`, `HarnessBuildContext`, `ExecutableAgent`, `CapabilityTypeRegistration`, `CapabilityTypeCatalog`, `SubagentDefinition`, `BuiltSubagent`, `SubagentCollection`, `DelegationContextPolicy`                                                                                                                                                                                                                                                                                                                                       |
| Context and identity  | `RunBindings`, `AgentContext`, `RunSkillPaths`, `SkillPath`, `ToolMetadataKey`, `ToolRuntimeMetadata`, `AbstractModelContextCapability`, `ModelContextRunBinding`, `ModelContextRequestKind`, `ModelContextInputOrigin`, `ModelContextPlacement`, `ModelContextProjectionRequest`, `ModelContextBlock`, `ModelContextProjection`, `ModelContextNext`, `AgentIdentityRef`, `AgentInstanceRef`, `AgentInstanceContext`                                                                                                                                                                                  |
| Input                 | `NativeRunInput`, `RunInputValue`, `SemanticRunInput`, `RunInputFactory`, `RunPreparationContext`, `DeferredToolResume`                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Plugins               | `AbstractHarnessPlugin`, `PluginOrdering`, `BoundPluginContext`, `PluginRunExchange`, `PluginRunNext`, `PluginRunResponse`, plugin configuration environment/default-path constants, `HarnessPluginConfiguration`, `HarnessPluginConfigurationEntry`, `HarnessPluginFactoryContext`, `HARNESS_PLUGIN_ENTRY_POINT_GROUP`, `HarnessPluginFactory`, `HarnessPluginFactoryCatalog`, factory registration/provenance values, `discover_harness_plugin_factory_references`, `build_harness_plugin_factory_catalog`                                                                                          |
| Models and recovery   | `ModelRunBinding`, `SelfHealingModel`, `ModelRecoveryRule`, `ModelRecoveryPolicy`, `RecoveryPromptFactory`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Filters               | `MessageIntegrityFilterCapability`, `ContentFilterCapability`, `ContentFilterConfiguration`, `MediaFamily`, `ColdStartFilterCapability`, `ColdStartFilterConfiguration`                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Context capabilities  | `RuntimeContextCapability`, `RuntimeContextConfiguration`, `WorkspaceOutlineCapability`, `WorkspaceOutlineConfiguration`, `FileContextCapability`, `FileContextConfiguration`, `HandoffCapability`, `HandoffConfiguration`, `CompactionCapability`, `CompactionPolicy`                                                                                                                                                                                                                                                                                                                                |
| Environment           | `EnvironmentRunBinding`, `CompositeEnvironmentRunBinding`, `NoopEnvironmentRunBinding`, `BoundEnvironment`, `NoopBoundEnvironment`, `EnvironmentRunExtension`, `EnvironmentRunExtensionContext`, `EnvironmentProviderBinding`, `EnvironmentTopologyController`, run-extension factory/catalog/registration/provenance values, run-extension discovery/catalog builders, `create_environment_provider_binding`, `create_environment_run_binding`, `create_noop_environment_run_binding`, topology/binding/readiness/file-scope/operation values, opaque process/output scalars, and `EnvironmentError` |
| State                 | `HarnessState`, `EnvironmentState`, `AgentContextState`, `AgentContextStateSnapshot`, `CapabilityState`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Execution and results | `HarnessRunStream`, `HarnessEvent`, `HarnessExtensionEvent`, `HarnessEventEmitter`, `HarnessRunResultEvent`, `HarnessRunResult`, `HarnessStreamEvent`, `SafeFailure`                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Usage                 | `UsageMeasure`, `ProviderUsage`, `BoundedRequestUsage`, `ModelUsageRecord`, `ProviderUsageRecord`, `UsageRecord`, `RunUsageLedger`, `ModelCostInput`, `ModelCostCalculator`, `ModelCostRunCapability`                                                                                                                                                                                                                                                                                                                                                                                                 |
| Skills                | `SkillManager`, `SkillSource`, `FileSkillSource`, `SkillMaterializer`, `SkillCatalogItem`, `BoundSkillCatalogItem`, `BoundSkillCatalog`, `SkillsPolicy`, `SkillsCapability`, `SkillSelectionRunCapability`                                                                                                                                                                                                                                                                                                                                                                                            |
| CodeAct               | `CodeActCapability`, `CodeActConfig`, `CodeActToolPolicy`, `CodeActPolicyToolset`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Errors                | Stable Harness error subclasses including `DefinitionError`, `ModelResolutionError`, `PluginError`, `RunError`, and `StateError`                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |

The Environment root also exports `EnvironmentAction` and `ENVIRONMENT_ACTION_CATALOG_VERSION`; provider-specific code uses the exact catalog values rather than copying action strings or deriving permission from operation families. Provider specification, Manager, built-in configuration, resource-state, attachment, and EIP session-source values are imported from `converge_agent_environment_provider`, not duplicated by the Harness facade. The Harness exports the provider-neutral `EnvironmentProviderBinding` contract and `create_environment_provider_binding()` adapter. Its concrete Direct Local and EIP binding classes, file/shell/session/transport adapters, and conversion-only enforcement values remain package-owned. First-party built-ins enter through provider-package attachments; trusted code can implement the provider-neutral binding contract directly for a custom process-local integration without creating another built-in lifecycle path. The programmatic opaque scalar exports are `OpaqueProcessHandle`, `OpaqueOutputReference`, and `OpaqueOutputCursor`; their bound wrappers and operation models are exported with the rest of the Environment value types, but no generic JSON serializer is exported for an opaque scalar.

Feature packages expose their definition-selected Capabilities, immutable configuration and domain values, provider protocols, fresh typed run collaborators, and reusable Toolsets where embedding code must compose them. The restricted CodeAct feature additionally exposes its owner-controlled `CodeActToolPolicy` and `CodeActPolicyToolset`; arbitrary model-visible metadata is not an eligibility API. A reusable Toolset may export a `ToolMetadataKey[T]` and its immutable value type for passive run-specific behavior supplied by built-in or external Capabilities; the package root additionally exports `FILE_VIEW_RULES` and `FileViewRule` because file-view behavior is an accepted cross-feature extension contract. This includes request/history Filters, Environment projection, runtime context, workspace outline, file context and handoff, monitored processes, skills, working state, structured user interaction, media/documents/web, usage attribution, invocation policy, and client tools. For provider-backed features, the reusable Toolset owns model-visible schemas, bounded results, progressive disclosure, and per-call semantics directly over natural provider-neutral ports. Its Capability owns definition/run binding, provenance, Agent-loop lifecycle or hooks, and Toolset composition. Provider protocols therefore model underlying domain operations rather than mirroring every model tool method. Media readers, document converters, Web clients/providers, and live Web policy are trusted fresh run attachments rather than definition state or portable values; every request still evaluates current policy. Model-facing JSON results use named `TypedDict` contracts alongside their owning Toolset surfaces. The `converge_agent_harness.toolsets` package publicly exposes `ToolOutputDisclosure`, `DEFAULT_TOOL_OUTPUT_CHARS`, `FINAL_TOOL_OUTPUT_HARD_CHARS`, `MAX_TOOL_OUTPUT_SPILL_BYTES`, and the shared strict character/byte measurement, fitting, spill, guidance, and acknowledgement helpers used to implement that contract; the process-local acknowledgement class itself and internal reference registries remain package-owned. These result and helper types are not duplicated across the package root unless another accepted contract names them as root exports. The `converge_agent_harness.tools` package exposes `HarnessToolMetadata`, including its backward-compatible optional `superseded_by_tool_ids` relation, while the mandatory resolver Capability and its wrapper remain Harness-owned infrastructure rather than Host composition APIs.

`DynamicEnvironmentCapability` is the optional model adapter over `BoundEnvironment`, not a provider lifecycle, current-topology projection, or authority type. It owns stable guidance, topology-change notices, model-context middleware participation, and composition of the public pure `FileToolset` and `ShellToolset`; their operation ports do not depend on the aggregate's concrete router. The internal retained-result projector remains package-owned, while emitted tool schemas and compact-reference behavior remain compatibility contracts. Fresh Host seams such as `TaskStateRunCapability`, `MonitoredProcessRunCapability`, `MediaRunCapability`, `DocumentsRunCapability`, `WebRunCapability`, and `ModelCostRunCapability` contribute no model behavior by themselves and never enter portable state. Capability, Model, Toolset, output, deferred-tool, message, event, usage, and usage-limit authors otherwise import upstream primitives directly from `pydantic_ai`.

## Build API

```python
@dataclass(frozen=True, slots=True)
class AgentDefinition[OutputT]:
    agent: AgentSpec
    output_type: OutputSpec[OutputT] | None
    definition_id: str = <UUID>
    model: Model | KnownModelName | str | None = None
    capabilities: tuple[AbstractCapability[AgentContext], ...] = ()
    plugins: tuple[AbstractHarnessPlugin, ...] = ()
    subagents: tuple[SubagentDefinition, ...] = ()
    self_healing: bool = True
    model_recovery: ModelRecoveryPolicy = ModelRecoveryPolicy()


class HarnessBuilder:
    def __init__(
        self,
        *,
        capability_type_catalog: CapabilityTypeCatalog | None = None,
        build_context: HarnessBuildContext | None = None,
        configured_plugins_enabled: bool | None = None,
    ) -> None: ...

    def build[OutputT](
        self,
        definition: AgentDefinition[OutputT],
    ) -> ExecutableAgent[OutputT]: ...

    @overload
    def build_code[OutputT](
        self,
        agent: AgentSpec,
        *,
        output_type: OutputSpec[OutputT],
        definition_id: str | None = None,
        model: Model | KnownModelName | str | None = None,
        capabilities: Sequence[AbstractCapability[AgentContext]] = (),
        plugins: Sequence[AbstractHarnessPlugin] = (),
        subagents: Sequence[SubagentDefinition] = (),
        self_healing: bool = True,
        model_recovery: ModelRecoveryPolicy | None = None,
    ) -> ExecutableAgent[OutputT]: ...

    @overload
    def build_code(
        self,
        agent: AgentSpec,
        *,
        output_type: None,
        definition_id: str | None = None,
        model: Model | KnownModelName | str | None = None,
        capabilities: Sequence[AbstractCapability[AgentContext]] = (),
        plugins: Sequence[AbstractHarnessPlugin] = (),
        subagents: Sequence[SubagentDefinition] = (),
        self_healing: bool = True,
        model_recovery: ModelRecoveryPolicy | None = None,
    ) -> ExecutableAgent[dict[str, JsonValue]]: ...
```

`HarnessBuilder` accepts at most one exact immutable custom Capability type catalog constructed by trusted Host code; `None` selects the canonical empty catalog. It also accepts one optional immutable `HarnessBuildContext` and an optional `configured_plugins_enabled` call-site override. An explicit context bypasses ambient source discovery; the override may enable or disable that context without changing its source, and enabling requires a contained configuration. Without a context, `None` follows the disabled-by-default environment switch, while `True` or `False` lets a trusted Host explicitly choose behavior for the executable it is creating. When enabled, the builder loads one bounded preferred YAML or supported JSON plugin document, imports only enabled factory keys into its own catalog, and creates fresh configured plugins for every recursively built definition. The decision is fixed before Agent construction; `run()` and `stream()` cannot partially toggle it. The detailed contract belongs to [Harness Plugin System](05-plugin-system.md#builder-application).

`build_code()` delegates to `build()`. Its explicit-output overload returns `ExecutableAgent[OutputT]`; its `output_type=None` overload requires `AgentSpec.output_schema` and returns `ExecutableAgent[dict[str, JsonValue]]`. Both use the one build-time construction contract in [Agent Definition and Build](03-agent-definition-and-build.md). Function tools and Toolsets enter only through native Capabilities. The builder accepts no arbitrary factory, import target, or Environment provider configuration.

## Run Bindings

```python
@dataclass(frozen=True, slots=True)
class RunBindings:
    instance: AgentInstanceContext
    environment: EnvironmentRunBinding
    model_binding: ModelRunBinding | None = None
    capabilities: tuple[
        AbstractCapability[AgentContext], ...
    ] = ()
    metadata: Mapping[str, JsonValue] = {}

    @classmethod
    def local(
        cls,
        *,
        identity: AgentIdentityRef | None = None,
        environment: EnvironmentRunBinding | None = None,
        model_binding: ModelRunBinding | None = None,
        capabilities: Sequence[
            AbstractCapability[AgentContext]
        ] = (),
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> RunBindings: ...
```

Bindings are fresh trusted inputs for one logical Harness run. Collection and metadata values are copied into immutable views. When `RunBindings.local()` receives no Environment, it calls `create_noop_environment_run_binding()` and never exposes a separate null/no-op execution branch or `is_noop` flag. `model_binding` is the only Harness-specific run-scoped model seam. It resolves a logical string to a native Model or raises; when absent, the thin resolver delegates to Pydantic's native inference.

`RunBindings.capabilities` are passed to every internal `ModelAttempt`. Feature-specific fresh attachments use documented public Capability types and stable IDs, and the Harness exposes each to its owning definition-selected Capability at the earliest lifecycle phase required by that feature. A policy value that changes run-specific instruction construction is captured before Capability preparation; live collaborators used only by tools resolve from Pydantic's finalized run Capability mapping. Missing, duplicate, or incompatible typed collaborators fail in the owner before dependent behavior. The Harness does not expose a second class-free registry or require role-name lookups. Identity, Environment, and model resolution remain explicit typed fields because they are universal run construction inputs rather than optional feature collaborators. In particular, an Environment binding cannot be moved into `RunBindings.capabilities`; `DynamicEnvironmentCapability` can be omitted without changing resource entry or controller availability.

`SkillsCapability()` uses the canonical optional workspace source owned by [Skills and Discovery](09-context-and-memory.md#skills-and-discovery). `SkillManager.default(additional_sources=...)` retains that source before explicit Host additions, while passing another `SkillManager` replaces the default source composition. `SkillSource.catalog(files=...)` and `SkillMaterializer.materialize(files=...)` use only the public `FileOperator` boundary. `SkillSource.roots` and `SkillManager.roots` expose the configured canonical absolute FileOperator paths. `SkillManager.scan(files=...)` is the direct non-virtual mode for a caller-controlled FileOperator. `SkillManager.scan_environment(environment=...)` captures revision-pinned file scopes and returns `BoundSkillCatalog`; both Environment-aware Hosts and `SkillsCapability` use that method without constructing another Agent loop. `BoundSkillCatalog.require_current()` fences only the routes represented by its items. These are separate explicit operations: neither dispatches to or retries through the other, and the first-version public surface provides no legacy source or root aliases.

`SkillSelectionRunCapability` is the optional Host override consumed only by a definition-selected `SkillsCapability`. Its immutable exact-name set chooses a subset of the conflict-resolved discovered catalog for one logical run. Absence selects the complete catalog, while an explicit empty set selects none. It carries no source, file, package, or activation authority and is reconstructed independently for resumed and child runs.

## Executable API

```python
class ExecutableAgent[OutputT]:
    definition: AgentDefinition[OutputT]
    subagents: SubagentCollection

    async def run(
        self,
        input: RunInputValue | None = None,
        *,
        input_factory: RunInputFactory | None = None,
        bindings: RunBindings,
        previous_state: HarnessState | None = None,
        deferred_resume: DeferredToolResume | None = None,
        usage: RunUsage | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> HarnessRunResult[OutputT]: ...

    def stream(
        self,
        input: RunInputValue | None = None,
        *,
        input_factory: RunInputFactory | None = None,
        bindings: RunBindings,
        previous_state: HarnessState | None = None,
        deferred_resume: DeferredToolResume | None = None,
        usage: RunUsage | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> HarnessRunStream[OutputT]: ...

    async def close(self) -> None: ...
```

An immediate input and `input_factory` are mutually exclusive. Omitting both passes no new user input, which permits continuation from imported messages. `deferred_resume` is a separate Harness correlation envelope around native Pydantic requests and results rather than user content. It requires a compatible prior state and current tool surface; after preflight, only its native results are consumed by the first `ModelAttempt`. A supplied `RunUsage` remains the one accumulator shared across all internal `ModelAttempt` values; otherwise the Harness creates a fresh value. Native `UsageLimits` are passed to every attempt and remain monotonic through the shared accumulator.

`run()` consumes the canonical stream internally and returns its sole terminal result. `stream()` returns a lazy single-entry async context manager and single-consumer async iterator. `ExecutableAgent` is itself an async context manager whose exit calls idempotent `close()`.

## Stream API

```python
class HarnessRunStream[OutputT](
    AsyncIterator[HarnessStreamEvent[OutputT]]
):
    thread_id: str
    run_id: str

    @property
    def context(self) -> AgentContext: ...

    @property
    def result(self) -> HarnessRunResult[OutputT] | None: ...

    @property
    def usage(self) -> RunUsage: ...

    def cancel(self) -> None: ...

    async def steer(self, input: RunInputValue) -> str: ...

    async def export_state(self) -> HarnessState: ...
```

Context entry allocates the Harness run ID, enters and publishes the initial Environment topology with its paired controller still non-active, restores compatible portable Environment state against that fixed snapshot, enters ordered Environment run extensions, activates the controller, optionally builds input, creates `AgentContext`, binds plugins, and builds the outer response. The controller remains valid until the logical terminal fence across every `ModelAttempt` and recovery backoff. No model or tool work begins until iteration reaches the inner Pydantic path.

The stream has exactly one consumer and forbids concurrent `__anext__()` calls. It yields normalized `HarnessEvent` values followed by at most one `HarnessRunResultEvent`. Public event sequence numbers are reassigned after plugin transformation and remain monotonic from zero.

`result` remains `None` until the terminal event is actually yielded. Leaving the context earlier establishes the terminal fence and closes resources without synthesizing a normal result. `cancel()` is idempotent and interrupts pre-start, active-attempt, or recovery-backoff work. `export_state()` is valid only while the stream is entered and not closed and includes Environment state collection linearized with topology publication.

`steer()` accepts one non-empty native `RunInputValue` while an inner Pydantic run is active, records it as user-authored input when compaction is enabled, and delivers it through public `RunContext.enqueue(..., priority="asap")`. It returns the native enqueue ID. Native Pydantic queue timing and `EnqueuedMessagesEvent` own active-run incorporation; the Harness adds no parallel delivery queue or applied-receipt state machine. A steering value retained immediately before an active-run boundary may be replayed by later compaction even when immediate native delivery cannot be confirmed. This context-first behavior deliberately prefers possible duplicate replay to silently losing accepted user intent.

The Harness exposes no cross-thread marshalling, when-idle queue, safe-pause state machine, durable command receipt, exact-once reconciliation, or Pydantic private run handle. `EnvironmentTopologyController` is deliberately a process-local Host mutation seam, not a durable command protocol. External mount authorization and durable command semantics remain Host concerns.

## Result

```python
class HarnessRunResult[OutputT]:
    @property
    def thread_id(self) -> str: ...
    @property
    def run_id(self) -> str: ...
    @property
    def status(
        self,
    ) -> Literal["completed", "suspended", "failed", "cancelled"]: ...
    @property
    def output(self) -> OutputT | None: ...
    @property
    def state(self) -> HarnessState | None: ...
    @property
    def usage(self) -> RunUsage: ...
    @property
    def usage_records(self) -> tuple[UsageRecord, ...]: ...
    @property
    def failure(self) -> SafeFailure | None: ...
    @property
    def suspend_reason(
        self,
    ) -> Literal["deferred"] | None: ...
    @property
    def deferred(self) -> DeferredToolRequests | None: ...

    def all_messages(self) -> tuple[ModelMessage, ...]: ...
    def new_messages(self) -> tuple[ModelMessage, ...]: ...
    def replace(self, ...) -> HarnessRunResult[Any]: ...
    def raise_for_status(self) -> None: ...
    def output_or_raise(self) -> OutputT: ...
```

All mutable values are copied on construction and access, including the complete nested `RunUsage` and mixed-source usage records. `all_messages()` and `new_messages()` decode detached copies. `replace()` preserves Thread and Run correlation and message views while allowing trusted middleware to replace terminal fields.

Valid field combinations are:

| Status      | Required                                                         | Excluded                                      |
| ----------- | ---------------------------------------------------------------- | --------------------------------------------- |
| `completed` | state, usage, output compatible with the built `OutputSpec`      | failure, suspension reason, deferred requests |
| `suspended` | state, usage, `suspend_reason="deferred"`, and deferred requests | output, failure                               |
| `failed`    | usage and `SafeFailure`; state when export succeeded             | output, suspension reason, deferred requests  |
| `cancelled` | usage; optional latest state                                     | output, failure, suspension reason, deferred  |

A completed output may legitimately be `None` when the output contract permits it. A result's private message view is independent from the optional continuation state under the trusted plugin contract; structural validation does not impose state provenance or equality.

`raise_for_status()` returns only for completion. `output_or_raise()` preserves a valid `None` output instead of using truthiness.

## Events

```python
class HarnessEvent(BaseModel):
    thread_id: str
    run_id: str
    sequence: int
    occurred_at: datetime
    event: AgentStreamEvent | HarnessExtensionEvent


class HarnessRunResultEvent(BaseModel):
    thread_id: str
    run_id: str
    sequence: int
    occurred_at: datetime
    result: HarnessRunResult[Any]
```

Ordinary events wrap validated public Pydantic AI stream events. Every event and result exposes the selected `thread_id` directly, so callers retain Thread correlation even when no result State is available. A terminal result event is emitted only after all Run resources close successfully. The event is a process-local completion observation, not a Host durable commit.

## Errors

```python
class SafeFailure(BaseModel):
    code: str
    message: str
    details: dict[str, JsonValue] = {}
    retry_hint: Literal[
        "none", "new_run", "dependency_change"
    ] = "none"
```

`HarnessError` subclasses represent stable caller-facing failures. Original exceptions remain protected causes only where the owning boundary permits their ordinary traceback rendering. Harness plugin configuration, metadata, import, constructor, and factory boundaries suppress raw exception chaining because those exceptions may contain configuration values, credentials, or private paths. Environment provider and run-extension factory errors expose only their bounded safe key, instance, and distribution fields while retaining package failures as protected causes. `SafeFailure` is the bounded terminal projection and contains no traceback or arbitrary object.

| Condition                                         | Public behavior                                                        |
| ------------------------------------------------- | ---------------------------------------------------------------------- |
| Definition, binding, input, or plugin setup error | Raise typed `HarnessError` before a result                             |
| `UsageLimitExceeded`                              | Failed result with `code="usage_limit_exceeded"`                       |
| Recognized terminal Agent execution failure       | Failed result with `code="agent_run_failed"`                           |
| Exhausted enabled semantic recovery               | Failed result with `code="model_recovery_exhausted"`                   |
| Native deferred output                            | Suspended result with `suspend_reason="deferred"`                      |
| Requested/native cancellation                     | Cancelled result when consumed through the normal stream               |
| External task cancellation                        | Propagate `asyncio.CancelledError` after cleanup                       |
| Trusted-code failure without a safe mapping       | Propagate after cleanup                                                |
| Failure after a valid candidate                   | Raise `RunCleanupError` carrying the nearest valid immutable candidate |

## Packaging and Compatibility

The base distribution depends on Pydantic, Pydantic AI, Pydantic Monty for optional run-local restricted CodeAct execution, PyYAML for the restricted preferred configuration-file form, the exact same-version `converge-agent-environment-provider`, and the compatible low-level EIP client. Module import loads no plugin or provider target, reads no environment variable or file, scans no entry point, performs no network request, reads no credential, and configures no global instrumentation. Harness plugin discovery/loading occurs through the explicit catalog API or an explicitly enabled builder/context loader. A disabled default builder reads only the enable variable and performs no file, metadata, or target access. Provider-package and Harness run-extension metadata loading remain explicit caller operations.

The public Python API, Harness state envelope, Environment provider-state codecs, Pydantic message codec, plugin contract, and model recovery rules evolve independently. The package tracks the repository-selected compatible Pydantic AI release and relies only on documented public Agent, Capability, Model, Toolset, message, deferred, event, output, and usage APIs.

Third-party provider factories, hosted adapters, managed-tool policy integrations, media/document converters, web/search providers, and observability exporters remain optional composition. Direct Local, Local Envd, Docker, and E2B provider support ships in the required provider package without extras, but importing either distribution initializes no SDK, subprocess, or resource. Local Envd receives one exact Host-resolved executable through its runtime collaborator; neither the provider package nor the low-level EIP client discovers or downloads it. Selecting a concrete optional Capability validates its required dependencies at construction and fails locally with a bounded configuration error; unrelated Harness imports and Agents remain usable.

Harness middleware packages may register factory classes under `converge_agent_harness.plugins`; aggregate run-extension packages may register factories under `converge_agent_harness.environment_run_extensions`. Environment provider factories register under `converge_agent_environment_provider.providers` and are loaded only through that package's explicit catalog. Explicit catalog APIs import only selected names into immutable catalogs. An opted-in builder constructs its own Harness plugin catalog from enabled document entries; it never scans or applies an Environment catalog. Factory results remain concrete code-first objects, and every other extension remains direct code-first composition. There is no process-global registration or import-time auto-enable behavior.

## Boundaries

| Concern                                               | Owner          |
| ----------------------------------------------------- | -------------- |
| Agent loop and native extension primitives            | Pydantic AI    |
| Public facade, plugin document, and process-local run | Harness        |
| Durable Agent definitions, artifact locks, execution  | Host           |
| Provider and Environment implementation               | Owning package |

## Trade-offs

### Small Code-first Facade vs. a Universal Schema

The facade preserves native Python composition and type fidelity. Hosted products must own stable schemas and adapters rather than expecting the library to serialize arbitrary extension objects.

### Async Execution vs. Sync Construction

Execution and cleanup are async because providers perform I/O. Construction remains synchronous; an explicitly enabled plugin context may perform bounded local file and package-metadata I/O before Agent composition.
