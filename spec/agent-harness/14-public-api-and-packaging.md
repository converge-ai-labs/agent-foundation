# Public API and Packaging

## Design Position

`agent-harness` is distributed as `a13n-harness`. Its public API is async for execution and cleanup, while Agent and Model construction is synchronous and code-first. It exposes native Pydantic AI types where upstream already owns the semantics and adds only the process-local definition, context, plugin, state, model-integration, recovery, event, and result boundaries shared by embedded and hosted callers.

The package does not expose a serialized Agent-definition language, compiler, or universal extension framework. Hosted systems reconstruct trusted direct Python inputs through their own adapters and call the same public builder as embedded applications. The Harness plugin boundary additionally owns one narrow versioned preferred YAML or supported JSON document and Build Context that can select factory classes during opted-in builder construction. Environment provider specifications, discovery, Providers, Resources, resource state, and attachments belong to `a13n-environment-provider`; the Harness owns attachment-to-runtime-mount adaptation and its separate run-extension discovery surface.

## Root Public Surface

The package root exports these contract groups:

| Group                 | Public values                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Definition and build  | `AgentSpec`, `ModelCapability`, `HarnessModelCharacteristics`, `AgentDefinition`, `HarnessBuilder`, `HarnessBuildContext`, `ExecutableAgent`, `CapabilityTypeRegistration`, `CapabilityTypeCatalog`, `SubagentDefinition`, `BuiltSubagent`, `SubagentCollection`, `DelegationContextPolicy`, `SubagentIdentityPolicy`, `derive_child_identity`                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Observation           | `HarnessInstrumentation`, `HarnessObservationContext`, `HarnessTraceContent`, `HARNESS_TRACE_LEVEL_ENV`, `HARNESS_TRACE_CONTENT_ENV`, `HARNESS_METRICS_ENV`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Context and identity  | `RunBindings`, `AgentContext`, `RunSkillPaths`, `SkillPath`, `ToolMetadataKey`, `ToolRuntimeMetadata`, `AbstractModelContextCapability`, `ModelContextMiddleware`, `ModelContextRequestKind`, `ModelContextInputOrigin`, `ModelContextPlacement`, `ModelContextProjectionRequest`, `ModelContextBlock`, `ModelContextProjection`, `ModelContextNext`, `AgentIdentityRef`, `AgentInstanceRef`, `AgentInstanceContext`, `MCPHeadersFactory`, `MCPContextHeaderBinding`, `MCPContextHeadersConfig`, `MCPContextHeaders`, `ContextualMCP`                                                                                                                                                                                                                                                                           |
| Input                 | `NativeRunInput`, `RunInputValue`, `SemanticRunInput`, `RunInputFactory`, `RunPreparationContext`, `DeferredToolResume`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Plugins               | `AbstractHarnessPlugin`, `PluginOrdering`, `BoundPluginContext`, `PluginRunExchange`, `PluginRunNext`, `PluginRunResponse`, plugin configuration environment/default-path constants, `HarnessPluginConfiguration`, `HarnessPluginConfigurationEntry`, `HarnessPluginFactoryContext`, `HARNESS_PLUGIN_ENTRY_POINT_GROUP`, `HarnessPluginFactory`, `HarnessPluginFactoryCatalog`, factory registration/provenance values, `discover_harness_plugin_factory_references`, `build_harness_plugin_factory_catalog`                                                                                                                                                                                                                                                                                                    |
| Models and recovery   | `infer_model`, `create_model_http_client`, `ModelHttpRetryConfig`, `DEFAULT_MODEL_HTTP_RETRY_CONFIG`, `DEFAULT_MODEL_HTTP_RETRY_STATUS_CODES`, `ModelProviderFactory`, `GatewayModelProviderFactory`, `ModelPatch`, `RequestHeadersModel`, `ModelCharacteristicsTransform`, `ModelCharacteristicsAlias`, `ModelCharacteristicsAliasCatalog`, `get_model_characteristics_alias_catalog`, `resolve_model_characteristics`, `ModelSettingsTransform`, `ModelSettingsAlias`, `ModelSettingsAliasCatalog`, `get_model_settings_alias_catalog`, `resolve_model_settings`, `RunModelResolver`, `OfficialModelEntry`, `OfficialModelCatalog`, `get_official_model`, `get_official_model_catalog`, `SelfHealingModelCapability`, `SelfHealingModel`, `ModelRecoveryRule`, `ModelRecoveryPolicy`, `RecoveryPromptFactory` |
| Filters               | `MessageIntegrityFilterCapability`, `ContentFilterCapability`, `ContentFilterConfiguration`, `MediaFamily`, `ColdStartFilterCapability`, `ColdStartFilterConfiguration`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Content capabilities  | Media, document, and Web Capability families; the Environment file multimedia-understanding provider, requests, results, run override, and image/video/audio model and settings environment constants; `WebConfiguration`, named Web backend bindings, Web selection environment constants, fresh run collaborators, provider protocols, and reusable Toolsets                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Context capabilities  | `RuntimeContextCapability`, `RuntimeContextConfiguration`, `WorkspaceOutlineCapability`, `WorkspaceOutlineConfiguration`, `FileContextCapability`, `FileContextConfiguration`, `HandoffCapability`, `HandoffConfiguration`, `CompactionCapability`, `CompactionPolicy`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Environment           | `Environment`, `EnvironmentSource`, `EnvironmentEntry`, `EnvironmentMount`, `EnvironmentAccess`, `EnvironmentAction`, `EnvironmentPermissionSet`, mount/readiness/path/operation observation values, opaque process/output scalars, `ProcessIdentity`, `ProcessEvent`, `ProcessEventKind`, `ProcessEventHook`, `ProcessManager`, `ManagedProcessState`, `ProcessManagerState`, `EnvironmentRunCallback`, `EnvironmentRunCallbacks`, run-extension definition values, and `EnvironmentError`                                                                                                                                                                                                                                                                                                                     |
| State                 | `HarnessState`, `EnvironmentState`, `AgentContextState`, `AgentContextStateSnapshot`, `CapabilityState`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Execution and results | `HarnessRunStream`, `AgentStreamEventProtocol`, `HarnessEvent`, `HarnessExtensionEvent`, `HarnessEventEmitter`, `ToolExtraEventPayload`, `FileChangeProjection`, `FilesystemChangedValue`, `emit_tool_event`, `HarnessRunResultEvent`, `HarnessRunResult`, `HarnessStreamEvent`, `SafeFailure`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Usage and pricing     | `UsageMeasure`, `ProviderUsage`, `BoundedRequestUsage`, `ModelUsageRecord`, `ProviderUsageRecord`, `UsageRecord`, `RunUsageLedger`, `CostSource`, `PricingStatus`, `ModelCostInput`, `ModelCostQuote`, `PriceTier`, `PriceComponent`, `PricingConstraint`, `ModelPriceRule`, `ModelPricingEntry`, `PricingCatalog`, `AbstractModelCostCapability`, `CatalogModelCostCapability`, `NoModelCostCapability`, `get_default_pricing_catalog`                                                                                                                                                                                                                                                                                                                                                                         |
| Skills                | `SkillManager`, `SkillSource`, `FileSkillSource`, `SkillMaterializer`, `SkillCatalogItem`, `BoundSkillCatalogItem`, `BoundSkillCatalog`, `SkillsPolicy`, `SkillsCapability`, `SkillSelectionRunCapability`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| CodeAct               | `CodeActCapability`, `CodeActConfig`, `CodeActToolPolicy`, `CodeActPolicyToolset`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Errors                | Stable Harness error subclasses including `DefinitionError`, `ModelResolutionError`, `PluginError`, `RunError`, and `StateError`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |

The Environment-file multimedia group consists of `AgentMediaUnderstandingProvider`, `MediaUnderstandingProvider`, `MediaUnderstandingRequest`, `MediaUnderstandingResult`, `MediaUnderstandingError`, `NativeInputMediaKind`, `FileMediaUnderstandingRunCapability`, and the `IMAGE_UNDERSTANDING_MODEL_ENV`, `VIDEO_UNDERSTANDING_MODEL_ENV`, `AUDIO_UNDERSTANDING_MODEL_ENV`, `IMAGE_UNDERSTANDING_MODEL_SETTINGS_ENV`, `VIDEO_UNDERSTANDING_MODEL_SETTINGS_ENV`, and `AUDIO_UNDERSTANDING_MODEL_SETTINGS_ENV` constants. These values are available from the package root; the provider protocol and value types are also available from `a13n_harness.toolsets`.

The package root exports the developer-facing Environment facade and source/mount values used directly by `run()` and `stream()`. It also exports `EnvironmentAction`, `EnvironmentPermissionSet`, and `ENVIRONMENT_ACTION_CATALOG_VERSION`; provider-specific code uses exact catalog values rather than copying action strings or deriving permission from operation families. Provider specification, Provider, Resource, built-in configuration, resource-state, attachment, and EIP session-source values are imported from `a13n_environment_provider`, not duplicated by the Harness facade. Native MCP transport and Toolsets remain imported from Pydantic AI. The Harness additionally exports one context-header configuration, resolver, and factory that construct fresh upstream MCP values with run-resolved headers; it does not publish another MCP client, protocol, or Toolset implementation.

Low-level runtime construction is intentionally separated under `a13n_harness.environment.advanced`. That module exports `EnvironmentProviderBinding`, `EnvironmentProviderOperations`, `EnvironmentRuntimeMount`, `EnvironmentRuntime`, `ManagedEnvironmentRuntime`, `EmptyEnvironmentRuntime`, `CompositeBoundEnvironment`, `NoopBoundEnvironment`, runtime and state limits, attachment adaptation, and runtime factory functions for trusted Hosts, run-extension authors, and custom provider-neutral integrations. These values are not package-root exports. Concrete Direct Local and EIP binding classes, file/shell/session/transport adapters, and conversion-only enforcement values remain package-owned.

The programmatic opaque scalar exports are `OpaqueProcessHandle`, `OpaqueOutputReference`, and `OpaqueOutputCursor`; their bound wrappers and operation models are exported with the rest of the Environment value types, but no generic JSON serializer is exported for an opaque scalar.

Feature packages expose their definition-selected Capabilities, immutable configuration and domain values, provider protocols, fresh typed run collaborators, and reusable Toolsets where embedding code must compose them. The restricted CodeAct feature additionally exposes its owner-controlled `CodeActToolPolicy` and `CodeActPolicyToolset`; arbitrary model-visible metadata is not an eligibility API. A reusable Toolset may export a `ToolMetadataKey[T]` and its immutable value type for passive run-specific behavior supplied by built-in or external Capabilities; the package root additionally exports `FILE_VIEW_RULES` and `FileViewRule` because file-view behavior is an accepted cross-feature extension contract. This includes request/history Filters, Environment projection, runtime context, workspace outline, file context and handoff, shell/process tools, skills, working state, structured user interaction, media/documents/web, usage attribution, invocation policy, and client tools. For provider-backed features, the reusable Toolset owns model-visible schemas, bounded results, progressive disclosure, and per-call semantics directly over natural provider-neutral ports. Its Capability owns definition/run binding, provenance, Agent-loop lifecycle or hooks, and Toolset composition. Provider protocols therefore model underlying domain operations rather than mirroring every model tool method. Media readers, document converters, Web clients/providers, and live Web policy are trusted fresh run attachments rather than definition state or portable values; every request still evaluates current policy. Model-facing JSON results use named `TypedDict` contracts alongside their owning Toolset surfaces. The `a13n_harness.toolsets` package publicly exposes `ToolOutputDisclosure`, `DEFAULT_TOOL_OUTPUT_CHARS`, `FINAL_TOOL_OUTPUT_HARD_CHARS`, `MAX_TOOL_OUTPUT_SPILL_BYTES`, and the shared strict character/byte measurement, fitting, spill, guidance, and acknowledgement helpers used to implement that contract; the process-local acknowledgement class itself and internal reference registries remain package-owned. These result and helper types are not duplicated across the package root unless another accepted contract names them as root exports. The `a13n_harness.tools` package exposes `HarnessToolMetadata`, including its backward-compatible optional `superseded_by_tool_ids` relation, while the mandatory resolver Capability and its wrapper remain Harness-owned infrastructure rather than Host composition APIs.

`DynamicEnvironmentCapability` is the optional model adapter over `BoundEnvironment`, not a provider lifecycle, current-mount projection, or authority type. It owns mount-change notices, model-context middleware participation, composition of the public pure `FileToolset` and `ShellToolset`, and one portable managed-process projection in `AgentContextState`; each Toolset owns the stable guidance for its active tools, and their operation ports do not depend on the aggregate's concrete router. `ShellToolset` uses the public `ProcessManager` to preserve exact `process-N` identity and unread offsets across compatible Thread continuations, lazily rebind through current authority, observe provider completion during an entered Turn, and deliver native enqueue hints. Constructor-supplied `ProcessEventHook` values receive additional non-authoritative event hints with Thread, Run, and Agent-instance correlation; they are not process authority, durable subscriptions, or portable state. The portable state models and exact rebinding behavior are public contracts, while the retained-result projector remains internal. Fresh Host seams such as `TaskStateRunCapability`, `MediaRunCapability`, `DocumentsRunCapability`, and `WebRunCapability` contribute no model behavior by themselves and never enter portable state. Model-cost policy is instead a build-time default-on `AbstractModelCostCapability` role: the Builder inserts the catalog implementation unless build code supplies exactly one custom or explicit no-cost implementation. Capability, Model, Toolset, output, deferred-tool, message, event, usage, and usage-limit authors otherwise import upstream primitives directly from `pydantic_ai`.

## Build API

```python
@dataclass(frozen=True, slots=True)
class AgentDefinition[OutputT]:
    agent: AgentSpec
    output_type: OutputSpec[OutputT] | None
    definition_id: str = <UUID>
    model: Model | None = None
    capabilities: tuple[AbstractCapability[AgentContext], ...] = ()
    plugins: tuple[AbstractHarnessPlugin, ...] = ()
    subagents: tuple[SubagentDefinition, ...] = ()
    model_recovery: ModelRecoveryPolicy = ModelRecoveryPolicy()

    def with_updates(
        self,
        updates: Mapping[str, object] | None = None,
        /,
        **overrides: object,
    ) -> Self: ...


class HarnessBuilder:
    def __init__(
        self,
        *,
        capability_type_catalog: CapabilityTypeCatalog | None = None,
        build_context: HarnessBuildContext | None = None,
        configured_plugins_enabled: bool | None = None,
        instrumentation: HarnessInstrumentation | Literal["environment"] | None = "environment",
        gateway_provider_factory: GatewayModelProviderFactory | None = None,
    ) -> None: ...

    @overload
    def build[OutputT](
        self,
        definition: AgentDefinition[OutputT],
        /,
    ) -> ExecutableAgent[OutputT]: ...

    @overload
    def build[OutputT](
        self,
        spec: AgentSpec,
        /,
        *,
        output_type: OutputSpec[OutputT],
        definition_id: str | None = None,
        model: Model | None = None,
        capabilities: Sequence[AbstractCapability[AgentContext]] = (),
        plugins: Sequence[AbstractHarnessPlugin] = (),
        subagents: Sequence[SubagentDefinition] = (),
        model_recovery: ModelRecoveryPolicy | None = None,
    ) -> ExecutableAgent[OutputT]: ...

    @overload
    def build(
        self,
        spec: AgentSpec,
        /,
        *,
        output_type: None,
        definition_id: str | None = None,
        model: Model | None = None,
        capabilities: Sequence[AbstractCapability[AgentContext]] = (),
        plugins: Sequence[AbstractHarnessPlugin] = (),
        subagents: Sequence[SubagentDefinition] = (),
        model_recovery: ModelRecoveryPolicy | None = None,
    ) -> ExecutableAgent[dict[str, JsonValue]]: ...
```

`HarnessBuilder` accepts at most one exact immutable custom Capability type catalog constructed by trusted Host code; `None` selects the canonical empty catalog. It also accepts one optional immutable `HarnessBuildContext`, an optional `configured_plugins_enabled` call-site override, an Observation selection, and an optional `GatewayModelProviderFactory` used by its default string-model inference path. Instrumentation is fixed across the recursively built executable graph; the default `"environment"` selection resolves bounded Harness policy and Host-configured global providers, explicit `HarnessInstrumentation` supports trace-only, metrics-only, and combined profiles, and explicit `None` keeps Harness Observation inert regardless of environment. The complete level/content/metric contract belongs to [Harness Observation](19-observation-model.md#instrumentation-contract). An explicit context bypasses ambient source discovery; the override may enable or disable that context without changing its source, and enabling requires a contained configuration. Without a context, `None` follows the disabled-by-default environment switch, while `True` or `False` lets a trusted Host explicitly choose behavior for the executable it is creating. When enabled, the builder loads one bounded preferred YAML or supported JSON plugin document, imports only enabled factory keys into its own catalog, and creates fresh configured plugins for every recursively built definition. The decision is fixed before Agent construction; `run()` and `stream()` cannot partially toggle it. The detailed plugin contract belongs to [Harness Plugin System](05-plugin-system.md#builder-application).

`build()` accepts either an already assembled `AgentDefinition` or an `AgentSpec` plus the same code-first construction inputs. The explicit-output overload returns `ExecutableAgent[OutputT]`; the `output_type=None` overload requires `AgentSpec.output_schema` and returns `ExecutableAgent[dict[str, JsonValue]]`. String model selection belongs to `AgentSpec.model`, while the optional `model=` argument accepts only a concrete Model and is mutually exclusive with that selection. `AgentSpec.with_updates()` is the public immutable preset-refinement operation: it deep-copies retained and replacement values, accepts field names and serialization aliases, performs exact top-level replacement, and validates the complete result before build. `AgentDefinition.with_updates()` applies the corresponding exact replacement to the complete process-local definition while preserving constructor-specific copy and native-object identity semantics. Every overload uses the one build-time construction contract in [Agent Definition and Build](03-agent-definition-and-build.md). Function tools and Toolsets enter only through native Capabilities. Apart from the typed gateway Provider seam, the builder accepts no arbitrary factory, import target, or Environment provider configuration.

## Run Bindings

```python
@dataclass(frozen=True, slots=True)
class RunBindings:
    instance: AgentInstanceContext
    environment: EnvironmentRuntime | None = None
    model_resolver: RunModelResolver | None = None
    toolset_instructions: bool | None = None
    capabilities: tuple[
        AbstractCapability[AgentContext], ...
    ] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    model_context: ModelContextMiddleware | None = None
    observation: HarnessObservationContext | None = None

    @classmethod
    def embedded(
        cls,
        *,
        identity: AgentIdentityRef | None = None,
        environment: EnvironmentRuntime | None = None,
        model_resolver: RunModelResolver | None = None,
        toolset_instructions: bool | None = None,
        model_context: ModelContextMiddleware | None = None,
        capabilities: Sequence[
            AbstractCapability[AgentContext]
        ] = (),
        metadata: Mapping[str, JsonValue] | None = None,
        observation: HarnessObservationContext | None = None,
    ) -> RunBindings: ...
```

`AgentIdentityRef` accepts fixed `issuer` and `subject` values plus arbitrary non-blank string claims supplied as keyword arguments. Its immutable `claims` view, `get_claim()`, and `require_claim()` are the public generic access surface; `user_id` and `agent_id` are conventional claim keys rather than fixed fields. Claim order does not affect identity equality. Claims contain no credential and are not restored from `HarnessState`.

`RunBindings` values are fresh trusted inputs for one logical Harness run. Collection and metadata values are copied into immutable views. `observation` is the optional bounded `HarnessObservationContext` projected only onto the logical-run span under the [Observation contract](19-observation-model.md#attribute-model); it is distinct from arbitrary model-facing or integration metadata. `RunBindings.embedded()` creates an embedded identity and optional advanced integrations; when a run supplies neither high-level Environment input nor an advanced runtime, run normalization calls `create_empty_environment_runtime()` and never exposes a separate null/no-op execution branch or `is_noop` flag. `toolset_instructions` is the optional runtime override for the Harness `AgentSpec.toolset_instructions` default; it controls only Toolset-owned instructions as defined by [Context and Memory](09-context-and-memory.md#toolset-instruction-enablement). `model_resolver` is the explicit run-scoped model-selection seam. It accepts an async callable conforming structurally to `RunModelResolver`; no subclass or registration is required. The callable resolves a logical string to a native Model or raises, and when absent the thin resolver calls Harness `infer_model()` with the builder's optional gateway Provider factory. `model_context` is the optional fresh Host wrapper around this run's model-context projection chain defined by [Context and Memory](09-context-and-memory.md#model-context-projection-contract).

`RunBindings.capabilities` are passed to every internal `ModelAttempt`. Feature-specific fresh attachments use documented public Capability types and stable IDs, and the Harness exposes each to its owning definition-selected Capability at the earliest lifecycle phase required by that feature. A policy value that changes run-specific instruction construction is captured before Capability preparation; live collaborators used only by tools resolve from Pydantic's finalized run Capability mapping. Missing, duplicate, or incompatible typed collaborators fail in the owner before dependent behavior. The Harness does not expose a second class-free registry or require role-name lookups. Identity, Environment, and model resolution remain explicit typed fields because they are universal run construction inputs rather than optional feature collaborators. In particular, an `EnvironmentRuntime` cannot be moved into `RunBindings.capabilities`; `DynamicEnvironmentCapability` can be omitted without changing resource entry or Host mutation authority.

`SkillsCapability()` uses the canonical optional workspace source owned by [Skills and Discovery](09-context-and-memory.md#skills-and-discovery). `SkillManager.default(additional_sources=...)` retains that source before explicit Host additions, while passing another `SkillManager` replaces the default source composition. `SkillSource.catalog(files=...)` and `SkillMaterializer.materialize(files=...)` use only the public `FileOperator` boundary. `SkillSource.roots` and `SkillManager.roots` expose the configured canonical absolute FileOperator paths. `SkillManager.scan(files=...)` is the direct non-virtual mode for a caller-controlled FileOperator. `SkillManager.scan_environment(environment=...)` captures mount-incarnation-pinned file scopes and returns `BoundSkillCatalog`; both Environment-aware Hosts and `SkillsCapability` use that method without constructing another Agent loop. `BoundSkillCatalog.require_current()` fences only the routes represented by its items. These are separate explicit operations: neither dispatches to or retries through the other, and the public surface provides only the explicit source and root operations described here.

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

Context entry allocates the Harness run ID, enters and publishes the initial Environment mount set while the runtime is non-active, restores compatible portable Environment state against that fresh mount set, enters ordered Environment run extensions, activates the runtime, optionally builds input, creates `AgentContext`, binds plugins, and builds the outer response. Host mutation through the same runtime remains valid until the logical terminal fence across every `ModelAttempt` and recovery backoff. No model or tool work begins until iteration reaches the inner Pydantic path.

The stream has exactly one consumer and forbids concurrent `__anext__()` calls. It yields normalized `HarnessEvent` values followed by at most one `HarnessRunResultEvent`. Public event sequence numbers are reassigned after plugin transformation and remain monotonic from zero.

`result` remains `None` until the terminal event is actually yielded. Leaving the context earlier establishes the terminal fence and closes resources without synthesizing a normal result. `cancel()` is idempotent and interrupts pre-start, active-attempt, or recovery-backoff work. `export_state()` is valid only while the stream is entered and not closed and includes Environment state collection linearized with mount publication.

`steer()` accepts one non-empty native `RunInputValue` while an inner Pydantic run is active, records it as user-authored input when compaction is enabled, and delivers it through public `RunContext.enqueue(..., priority="asap")`. It returns the native enqueue ID. Native Pydantic queue timing and `EnqueuedMessagesEvent` own active-run incorporation; the Harness adds no parallel delivery queue or applied-receipt state machine. A steering value retained immediately before an active-run boundary may be replayed by later compaction even when immediate native delivery cannot be confirmed. This context-first behavior deliberately prefers possible duplicate replay to silently losing accepted user intent.

The Harness exposes no cross-thread marshalling, when-idle queue, safe-pause state machine, durable command receipt, exact-once reconciliation, or Pydantic private run handle. `EnvironmentRuntime` is a process-local Host mutation authority, not a durable command protocol. External mount authorization and durable command semantics remain Host concerns.

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
| Root native deferred output                       | Suspended result with `suspend_reason="deferred"`                      |
| Child runtime deferral                            | Complete denied tool results; the same child Run continues             |
| Unexpected child terminal deferred output         | Failed result with `code="subagent_deferred_unsupported"`              |
| Requested/native cancellation                     | Cancelled result when consumed through the normal stream               |
| External task cancellation                        | Propagate `asyncio.CancelledError` after cleanup                       |
| Trusted-code failure without a safe mapping       | Propagate after cleanup                                                |
| Failure after a valid candidate                   | Raise `RunCleanupError` carrying the nearest valid immutable candidate |

## Packaging and Compatibility

The base distribution depends on Pydantic, the full Pydantic AI distribution, and the Pydantic AI extras required by its common direct-model and realtime Provider integrations, together with the OpenTelemetry API used by the public tracer/meter provider, no-op signal, span, and metric boundaries, `genai-prices` for normalized usage-dimension pricing, Pydantic Monty for optional run-local restricted CodeAct execution, PyYAML for restricted package/configuration data, the exact same-version `a13n-environment-provider`, and the compatible low-level EIP client. A base installation therefore includes the upstream client dependencies for Anthropic, OpenAI, Google, Bedrock, Bedrock Mantle, Cohere, Groq, Hugging Face, Mistral, OpenRouter, and xAI, including supported realtime clients; OpenAI-compatible Providers reuse the installed OpenAI client. Credentials, endpoints, and Provider selection remain explicit caller configuration and package presence never enables a Provider. Local URL and stdio MCP composition requires no separate Harness extra; selecting a server remains explicit Agent definition behavior. The Harness does not configure the OpenTelemetry SDK, exporters, collector clients, Langfuse, or Logfire; a dependency made available by the full upstream distribution does not activate its integration. The wheel contains the official model catalog and Harness pricing replacement data. Module import loads no plugin or provider target, reads no environment variable or external file, scans no entry point, performs no network request, reads no credential, and configures no global instrumentation; package-local catalogs load lazily on explicit access or Builder default-cost construction. Harness plugin discovery/loading occurs through the explicit catalog API or an explicitly enabled builder/context loader. A disabled default builder reads only the enable variable and performs no file, metadata, or target access. Provider-package and Harness run-extension metadata loading remain explicit caller operations.

The public Python API, Harness Observation registry, Harness state envelope, Environment provider-state codecs, Pydantic message codec, plugin contract, and model recovery rules evolve independently. The package tracks the repository-selected compatible Pydantic AI release and relies only on documented public Agent, Capability, Model, Toolset, instrumentation, message, deferred, event, output, and usage APIs.

Additional third-party provider factories, hosted adapters, managed-tool policy integrations, media/document converters, web/search Provider clients, the OpenTelemetry SDK/exporters, and Langfuse or Logfire activation remain optional Host composition. Direct Local, Local Envd, Docker, and E2B provider support ships in the required provider package without extras, but importing either distribution initializes no SDK, subprocess, or resource. Local Envd receives one exact Host-resolved executable through its runtime collaborator; neither the provider package nor the low-level EIP client discovers or downloads it. Selecting a concrete optional Capability validates its required dependencies at construction and fails locally with a bounded configuration error; unrelated Harness imports and Agents remain usable.

Harness middleware packages may register factory classes under `a13n_harness.plugins`; aggregate run-extension packages may register factories under `a13n_harness.environment_run_extensions`. Environment provider factories register under `a13n_environment_provider.providers` and are loaded only through that package's explicit catalog. Explicit catalog APIs import only selected names into immutable catalogs. An opted-in builder constructs its own Harness plugin catalog from enabled document entries; it never scans or applies an Environment catalog. Factory results remain concrete code-first objects, and every other extension remains direct code-first composition. There is no process-global registration or import-time auto-enable behavior.

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
