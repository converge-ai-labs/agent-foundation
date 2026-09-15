# Public API and Packaging

## Design Position

`a13n-harness` is distributed as `a13n-harness`. Its public API is async for execution and run-scoped cleanup, while Agent and Model construction is synchronous and code-first. It exposes native Pydantic AI types where upstream already owns the semantics and adds only the process-local definition, context, plugin, state, model-integration, recovery, event, and result boundaries shared by embedded and hosted callers.

The package does not expose a serialized Agent-definition language, compiler, or universal extension framework. Hosted systems reconstruct trusted direct Python inputs through their own adapters and call the same public builder as embedded applications. The Harness plugin boundary additionally owns one narrow versioned preferred YAML or supported JSON document and Build Context that can select factory classes during explicitly enabled builder construction. Environment Provider specifications, discovery, Provider implementations, fresh adapters, and state codecs belong to `a13n-environment`; Harness accepts already constructed adapters and adds only lightweight mount policy plus Run-local routing. Public `EnvironmentMount.mount_path` optionally assigns a Host-selected aggregate/model-facing root while keeping `working_directory` and every Provider call in the provider-local namespace; the complete compatibility and routing contract belongs to [Environment Integration](08-environment-integration.md#run-inputs).

## Root Public Surface

The package root is a closed primary code-first facade. It exports only the values needed to define, build, run, observe, continue, and compose an Agent through the ordinary path:

| Group                         | Root exports                                                                                                                                                                                                              |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Version                       | `__version__`                                                                                                                                                                                                             |
| Definition and build          | `AgentSpec`, `ModelCapability`, `HarnessModelCharacteristics`, `AgentDefinition`, `HarnessBuilder`, `ExecutableAgent`, `SubagentDefinition`, `DelegationContextPolicy`, `SubagentIdentityPolicy`, `derive_child_identity` |
| Context and identity          | `RunBindings`, `AgentContext`, `AgentIdentityRef`, `AgentInstanceRef`, `AgentInstanceContext`                                                                                                                             |
| Input                         | `NativeRunInput`, `RunInputValue`, `SemanticRunInput`, `RunInputFactory`, `RunPreparationContext`, `DeferredToolResume`                                                                                                   |
| Environment selection         | `Environment`, `EnvironmentMount`, `EnvironmentAccess`                                                                                                                                                                    |
| Direct plugins                | `AbstractHarnessPlugin`, `PluginOrdering`                                                                                                                                                                                 |
| Model and recovery            | `infer_model`, `RunModelResolver`, `ModelRecoveryPolicy`, `ToolRecoveryMode`                                                                                                                                              |
| Observation                   | `HarnessInstrumentation`, `HarnessObservationContext`, `HarnessTraceContent`                                                                                                                                              |
| State, execution, and results | `HarnessState`, `HarnessRunStream`, `HarnessRunResult`, `SafeFailure`, `AgentStreamEventProtocol`, `HarnessEvent`, `HarnessExtensionEvent`, `HarnessRunResultEvent`, `HarnessStreamEvent`                                 |
| Errors                        | `HarnessError`, `DefinitionError`, `IdentityError`, `InputError`, `ModelResolutionError`, `PluginError`, `RunCleanupError`, `RunError`, `StateError`                                                                      |

`a13n_harness.__all__` is exactly this table. Feature-family APIs remain public through their owning stable modules rather than being duplicated at the package root. Important routes include:

| Module                               | Owned surface                                                                                    |
| ------------------------------------ | ------------------------------------------------------------------------------------------------ |
| `a13n_harness.capabilities`          | First-party Capability families and public Host boundaries, including the subagent operator      |
| `a13n_harness.capability_types`      | `CapabilityTypeCatalog` and declarative Capability registration                                  |
| `a13n_harness.context`               | Advanced run context, built-subagent, Skill-path, and tool-metadata values                       |
| `a13n_harness.environment`           | Mount policy, provider-neutral Run-local operations, and Environment Run Extension contracts     |
| `a13n_harness.environment.advanced`  | Explicit Run-local runtime construction over the same Environment mount inputs                   |
| `a13n_harness.environment.providers` | Advanced Host binding scopes, exact runtime mount ceilings, and entered aggregate contracts      |
| `a13n_harness.events`                | Event emission helpers and typed first-party payloads                                            |
| `a13n_harness.filters`               | First-party content and integrity filters                                                        |
| `a13n_harness.mcp`                   | MCP context-header integration                                                                   |
| `a13n_harness.model_auth`            | Codex request/login supplements and Grok OAuth sources, flows, lifecycle, and Model construction |
| `a13n_harness.model_catalog`         | Official model catalog values                                                                    |
| `a13n_harness.model_context`         | Model-context middleware and projection contracts                                                |
| `a13n_harness.models`                | Provider inference, request headers, transport, settings, and self-healing                       |
| `a13n_harness.observation`           | Observation configuration constants and advanced instrumentation values                          |
| `a13n_harness.plugin_configuration`  | Ambient YAML, JSON, and environment configuration plus Build Context                             |
| `a13n_harness.plugin_factories`      | Plugin factory discovery and catalogs                                                            |
| `a13n_harness.plugins`               | Complete plugin middleware protocol                                                              |
| `a13n_harness.pricing`               | Bundled/current pricing catalogs and model-cost Capability family                                |
| `a13n_harness.state`                 | Advanced context and Capability state values                                                     |
| `a13n_harness.tools`                 | Tool recovery declarations, managed tool invocation, and event helpers                           |
| `a13n_harness.toolsets`              | First-party reusable Toolsets, including the standard async subagent dispatcher                  |
| `a13n_harness.usage`                 | Usage attribution, ledger, and `intersect_usage_limits`                                          |

The Model authentication feature exports `CodexRequestModel`, `CodexLoginFlow`, `CodexLoginResult`, and the Codex device flow alongside Grok credential/source values, OAuth and refresh primitives, bounded errors, and `build_grok_model()`. Native Codex credential, source, provider, and ordinary browser-flow APIs are imported directly from `pydantic_ai.providers.openai_codex`; there are no compatibility aliases or parallel refresh APIs. Its lifecycle and Host boundary belong to [Model Authentication](16a-model-authentication.md).

A value is not private merely because it is absent from the root facade. Its owning module and that module's documented exports are the canonical import route. Removing duplicate root re-exports keeps discovery bounded and prevents unrelated feature families from becoming one coupled compatibility surface. The public Mem0 integration is imported as `from a13n_harness.capabilities import Mem0Capability, Mem0Scope`; its run replacement and Toolset implementation remain package-private. The public async-subagent boundary is imported from `a13n_harness.capabilities`: `SubagentCapability`, `SubagentOperator`, `SubagentOperatorContext`, `SubagentToolCallContext`, `SubagentDelegationPlan`, and the standard request/result/view models. `AsyncSubagentToolset` and the standard Environment Shell Toolset are available from `a13n_harness.toolsets`; the private inline executor and Run process controller are not public operator implementations.

Grouped discovery is imported as `ToolProxyCapability`, `ToolProxyConfig`, `ToolProxyGroup`, `ToolProxyPlan`, and `ToolProxySelection` from `a13n_harness.capabilities`. `ToolProxyCapability(groups=..., config=...)` installs concrete sources; each group descriptor supplies a native Toolset or Capability and a description. Alternatively, `AgentDefinition.tool_proxy` accepts an immutable build plan whose selections reference existing concrete Capabilities and exact Harness plugin instance IDs. Both use the same composition semantics. There is no separately installed public grouping wrapper. These are code-first composition APIs; they do not add a built-in declarative `AgentSpec` registration. Their behavior belongs to [Tool Execution](07-tool-execution.md#grouped-toolproxy-discovery).

Tool permission APIs are imported from `a13n_harness.tools`: `ToolIdentity`, `ToolIdentityToolset`, `source_tool_id`, `ToolPermissions`, `ToolPermissionsCapability`, and `ToolApprovalContext`. Generic review APIs are imported from `a13n_harness.capabilities`: `ToolRiskLevel`, `ToolReviewPolicy`, `ToolReviewRule`, `ToolReviewConfig`, `ToolReviewer`, `AgentToolReviewer`, `ToolReviewRequest`, `ToolReviewAssessment`, `ToolReviewResult`, and `ToolReviewError`. `ToolPermissionsCapability` is the sole permission/review Capability; review values and implementations are collaborators, not separately registered Capabilities. `WebDomainPolicy` is public from `a13n_harness.capabilities.web` for Host transports that enforce domain checks at each redirect. Behavior belongs to [Tool Execution](07-tool-execution.md#tool-permissions-and-review) and [Web Resources](09-context-and-memory.md#media-documents-and-web-resources), not the package root.

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
    tool_proxy: ToolProxyPlan | None = None
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
        *,
        pricing_catalog: PricingCatalog | None = None,
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
        tool_proxy: ToolProxyPlan | None = None,
        subagents: Sequence[SubagentDefinition] = (),
        model_recovery: ModelRecoveryPolicy | None = None,
        pricing_catalog: PricingCatalog | None = None,
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
        tool_proxy: ToolProxyPlan | None = None,
        subagents: Sequence[SubagentDefinition] = (),
        model_recovery: ModelRecoveryPolicy | None = None,
        pricing_catalog: PricingCatalog | None = None,
    ) -> ExecutableAgent[dict[str, JsonValue]]: ...
```

`HarnessBuilder` accepts at most one exact immutable custom Capability type catalog constructed by trusted Host code; `None` selects the canonical empty catalog. It also accepts one optional immutable `HarnessBuildContext`, an optional `configured_plugins_enabled` call-site override, an Observation selection, and an optional `GatewayModelProviderFactory` used by its default string-model inference path. Instrumentation is fixed across the recursively built executable graph; the default `"environment"` selection resolves bounded Harness policy and Host-configured global providers, explicit `HarnessInstrumentation` supports trace-only, metrics-only, and combined profiles, and explicit `None` keeps Harness Observation inert regardless of environment. The complete level/content/metric contract belongs to [Harness Observation](19-observation-model.md#instrumentation-contract). An explicit context bypasses ambient source discovery; the override may enable or disable that context without changing its source, and enabling requires a contained configuration. Without a context, `None` follows the disabled-by-default environment switch, while `True` or `False` lets a trusted Host explicitly choose behavior for the executable it is creating. When enabled, the builder loads one bounded preferred YAML or supported JSON plugin document, imports only enabled factory keys into its own catalog, and creates fresh configured plugins for every recursively built definition. The decision is fixed before Agent construction; `run()` and `stream()` cannot partially toggle it. The detailed plugin contract belongs to [Harness Plugin System](05-plugin-system.md#builder-application).

`build()` accepts either an already assembled `AgentDefinition` or an `AgentSpec` plus the same code-first construction inputs. The explicit-output overload returns `ExecutableAgent[OutputT]`; the `output_type=None` overload requires `AgentSpec.output_schema` and returns `ExecutableAgent[dict[str, JsonValue]]`. String model selection belongs to `AgentSpec.model`, while the optional `model=` argument accepts only a concrete Model and is mutually exclusive with that selection. `AgentSpec.with_updates()` is the public immutable preset-refinement operation: it deep-copies retained and replacement values, accepts field names and serialization aliases, performs exact top-level replacement, and validates the complete result before build. `AgentDefinition.with_updates()` applies the corresponding exact replacement to the complete process-local definition while preserving constructor-specific copy and native-object identity semantics. Every overload uses the one build-time construction contract in [Agent Definition and Build](03-agent-definition-and-build.md). Function tools and Toolsets enter only through native Capabilities. Apart from the typed gateway Provider seam, the builder accepts no arbitrary factory, import target, or Environment provider configuration.

## Run Bindings

```python
@dataclass(frozen=True, slots=True)
class RunBindings:
    instance: AgentInstanceContext
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

`RunBindings` values are fresh trusted inputs for one logical Harness Run. Collection and metadata values are copied into immutable views. Thread identity remains State-owned: a trusted Host selects it through `HarnessState.new(thread_id=...)` or derives a distinct branch through `HarnessState.fork(thread_id=...)`, never through fresh bindings. Environment adapters and ordered Environment Run Extensions are supplied through explicit `run()`/`stream()` arguments rather than retained in reusable bindings. Async subagent mode and its stable `SubagentOperator` remain definition-selected by `SubagentCapability`; the operator receives only an immutable authorized plan and detached parent correlation. Run-local shell observations requires no operator and accepts no process collaborator through bindings. `observation` is the optional bounded `HarnessObservationContext` projected only onto the logical-run span under the [Observation contract](19-observation-model.md#attribute-model); it is distinct from arbitrary model-facing or integration metadata. `RunBindings.embedded()` creates an embedded identity and optional advanced integrations; when a Run supplies no Environment input, normalization creates an empty bound facade and exposes no Environment tools. `toolset_instructions` is the optional runtime override for the Harness `AgentSpec.toolset_instructions` default; it controls only Toolset-owned instructions as defined by [Context and Memory](09-context-and-memory.md#toolset-instruction-enablement). `model_resolver` is the explicit run-scoped model-selection seam. It accepts an async callable conforming structurally to `RunModelResolver`; no subclass or registration is required. The callable resolves a logical string to a native Model or raises, and when absent the thin resolver calls Harness `infer_model()` with the builder's optional gateway Provider factory. `model_context` is the optional fresh Host wrapper around this run's model-context projection chain defined by [Context and Memory](09-context-and-memory.md#model-context-projection-contract).

`RunBindings.capabilities` are passed to every internal `ModelAttempt`. Feature-specific fresh collaborators use documented public Capability types and stable IDs, and the Harness exposes each to its owning definition-selected Capability at the earliest lifecycle phase required by that feature. A stable definition-selected operator cannot be replaced by a run Capability or change its Toolset. A policy value that changes run-specific instruction construction is captured before Capability preparation; live collaborators used only by tools resolve from Pydantic's finalized run Capability mapping. Missing, duplicate, or incompatible typed collaborators fail in the owner before dependent behavior. The Harness does not expose a second class-free registry or require role-name lookups. Identity and model resolution remain explicit typed fields because they are universal Run construction inputs rather than optional feature collaborators. Environment adapters remain explicit Run arguments and cannot be moved into `RunBindings.capabilities`; `DynamicEnvironmentCapability` can be omitted without changing adapter entry or trusted Run-local routing.

`SkillsCapability()` uses the canonical optional workspace source owned by [Skills and Discovery](09-context-and-memory.md#skills-and-discovery). `SkillManager.default(additional_sources=...)` retains that source before explicit Host additions, while passing another `SkillManager` replaces the default source composition. `SkillSource.catalog(files=...)` and `SkillMaterializer.materialize(files=...)` use only the public `FileOperator` boundary. `SkillSource.roots` and `SkillManager.roots` expose the configured canonical absolute FileOperator paths. `SkillManager.scan(files=...)` is the direct non-virtual mode for a caller-controlled FileOperator. `SkillManager.scan_environment(environment=...)` captures mount-incarnation-pinned file scopes and returns `BoundSkillCatalog`; both Environment-aware Hosts and `SkillsCapability` use that method without constructing another Agent loop. `BoundSkillCatalog.require_current()` fences only the routes represented by its items. These are separate explicit operations: neither dispatches to or retries through the other, and the public surface provides only the explicit source and root operations described here.

`RunBindings.skill_selection` is the optional Host override consumed only by a definition-selected `SkillsCapability`. Its immutable exact-name set chooses a subset of the conflict-resolved discovered catalog for one logical run. Absence selects the complete catalog, while an explicit empty set selects none. It carries no source, file, package, or activation authority and is reconstructed independently for resumed and child runs.

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
        environment: Environment | EnvironmentMount | None = None,
        environments: Mapping[str, Environment | EnvironmentMount] | None = None,
        default_environment: str | None = None,
        environment_run_extensions: Sequence[EnvironmentRunExtension] = (),
        previous_state: HarnessState | None = None,
        tool_recovery: ToolRecoveryMode = "declared",
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
        environment: Environment | EnvironmentMount | None = None,
        environments: Mapping[str, Environment | EnvironmentMount] | None = None,
        default_environment: str | None = None,
        environment_run_extensions: Sequence[EnvironmentRunExtension] = (),
        previous_state: HarnessState | None = None,
        tool_recovery: ToolRecoveryMode = "declared",
        deferred_resume: DeferredToolResume | None = None,
        usage: RunUsage | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> HarnessRunStream[OutputT]: ...

```

`ToolRecoveryMode`, exported from `a13n_harness`, is `Literal["declared", "never", "always"]`. `tool_recovery` selects replay or unknown-result closure as defined by [Restored Pending Tool Calls](10-snapshot-and-resume.md#restored-pending-tool-calls). It replaces `execute_pending_tools`; migrate `False` to `"never"`, `True` to `"always"`, and `"auto"` to `"declared"`. The default is declaration-based recovery.

`a13n_harness.tools.recovery_retryable(function_or_tool)` returns a native `Tool` carrying the recovery-retryable declaration. It supports decorator and function-call syntax. Existing tools are copied with their settings and other metadata preserved; the helper does not mutate its argument or wrap the execution function. `RECOVERY_RETRY_SAFE_METADATA_KEY` remains available for native tool metadata integrations.

An immediate input and `input_factory` are mutually exclusive. Omitting both passes no new user input, which permits continuation from imported messages. `deferred_resume` is a separate Harness correlation envelope around native Pydantic requests and results rather than user content. It requires a compatible prior state and current tool surface; after preflight, only its native results are consumed by the first `ModelAttempt`. A supplied `RunUsage` remains the one accumulator shared across all internal `ModelAttempt` values; otherwise the Harness creates a fresh value. Native `UsageLimits` are passed to every attempt and remain monotonic through the shared accumulator.

`run()` consumes the canonical stream internally and returns its sole terminal result. `stream()` returns a lazy single-entry async context manager and single-consumer async iterator. `ExecutableAgent` is immutable reusable build output and has no independent async lifecycle: it owns no entered Model, plugin, Capability, client, or child resource. Every resource-bearing lifetime belongs to a `HarnessRunStream` or to the caller that supplied the resource.

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
    def outcome(self) -> HarnessRunResult[OutputT] | None: ...

    @property
    def diagnostic_error(self) -> BaseException | None: ...

    @property
    def usage(self) -> RunUsage: ...

    def cancel(self) -> None: ...

    async def steer(self, input: RunInputValue) -> str: ...

    async def export_state(self) -> HarnessState: ...
```

Context entry allocates the Harness Run ID, restores the selected State-owned Thread ID or generates initial State, enters every fresh Environment adapter, atomically publishes the initial mount set, optionally builds input, creates `AgentContext`, binds plugins, and builds the outer response. Portable `environment_states` never restores an already entered adapter; a Host must select state before constructing the adapter. Trusted Run-local mutation remains valid until the terminal fence across every `ModelAttempt` and recovery backoff. No model or tool work begins until iteration reaches the inner Pydantic path.

The stream has exactly one consumer and forbids concurrent `__anext__()` calls. It yields normalized `HarnessEvent` values followed by at most one `HarnessRunResultEvent`. Public event sequence numbers are reassigned after plugin transformation and remain monotonic from zero.

`result` remains `None` until the terminal event is actually yielded. After shutdown, `outcome` exposes the nearest validated terminal candidate, including when cleanup failure or external cancellation prevented delivery; before shutdown it is `None`. This is the same candidate authority used by `RunCleanupError.outcome`, not a success receipt. Hosts recovering a suspended candidate preserve its state and deferred requests together and still propagate the original failure or cancellation. Leaving the context earlier establishes the terminal fence and closes resources without synthesizing a normal result. `cancel()` is idempotent and interrupts pre-start, active-attempt, or recovery-backoff work. While active, `export_state()` includes Environment state collection linearized with mount publication. After close it returns the detached shutdown checkpoint, or raises an explicit state-unavailable error if capture failed. `diagnostic_error` exposes the terminal exception only to trusted in-process Hosts for private diagnostics; it is not part of `SafeFailure`, events, state, or any serialized result.

`steer()` accepts one non-empty native `RunInputValue` while an inner Pydantic run is active, records it as user-authored input when compaction is enabled, and delivers it through public `RunContext.enqueue(..., priority="asap")`. It returns the native enqueue ID. Native Pydantic queue timing and `EnqueuedMessagesEvent` own active-run incorporation; the Harness adds no parallel delivery queue or applied-receipt state machine. A steering value retained immediately before an active-run boundary may be replayed by later compaction even when immediate native delivery cannot be confirmed. This context-first behavior deliberately prefers possible duplicate replay to silently losing accepted user intent.

The Harness exposes no cross-thread marshalling, when-idle queue, safe-pause state machine, durable command receipt, exactly-once reconciliation, or Pydantic private run handle. Run-local Environment mutation is not a durable command protocol and cannot change Host Environment association. External mount authorization and durable desired-mount semantics remain Host concerns.

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

`a13n_harness.model_context` exports native `ModelInputEvent` and `user_prompt_content` for metadata-preserving, media-payload-free presentation. `a13n_harness.capabilities` exports native `CompactionSummaryEvent`. `a13n_harness.toolsets.events` exports native `FileEditAppliedEvent`, `HandoffSummaryEvent`, and `ShellStatusEvent`. [Events and Usage](12-events-observability-and-usage.md) owns their content and lifecycle contracts.

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

`HarnessError` subclasses represent stable caller-facing failures. Original exceptions remain protected causes only where the owning boundary permits their ordinary traceback rendering. Harness plugin configuration, metadata, import, constructor, and factory boundaries suppress raw exception chaining because those exceptions may contain configuration values, credentials, or private paths. Environment entry and operation errors retain only the Provider package's bounded safe code and details while native failures remain protected causes. `SafeFailure` is the bounded terminal projection and contains no traceback or arbitrary object.

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

The base distribution depends on Pydantic, the full Pydantic AI distribution, and the Pydantic AI extras required by its common direct-model and realtime Provider integrations, together with the OpenTelemetry API used by the public tracer/meter provider, no-op signal, span, and metric boundaries, `genai-prices` for normalized usage-dimension pricing, Pydantic Monty for optional run-local restricted CodeAct execution, PyYAML for restricted package/configuration data, the exact same-version `a13n-environment`, and the compatible low-level EIP client. A base installation therefore includes the upstream client dependencies for Anthropic, OpenAI, Google, Bedrock, Bedrock Mantle, Cohere, Groq, Hugging Face, Mistral, OpenRouter, and xAI, including supported realtime clients; OpenAI-compatible Providers reuse the installed OpenAI client. Credentials, endpoints, and Provider selection remain explicit caller configuration and package presence never enables a Provider. Local URL and stdio MCP composition requires no separate Harness extra; selecting a server remains explicit Agent definition behavior. The Harness does not configure the OpenTelemetry SDK, exporters, collector clients, Langfuse, or Logfire; a dependency made available by the full upstream distribution does not activate its integration. The wheel contains the official model catalog and Harness pricing replacement data. Module import loads no plugin or provider target, reads no environment variable or external file, scans no entry point, performs no network request, reads no credential, and configures no global instrumentation; package-local catalogs load lazily on explicit access or Builder default-cost construction. Harness plugin discovery/loading occurs through the explicit catalog API or an explicitly enabled builder/context loader. A disabled default builder reads only the enable variable and performs no file, metadata, or target access. Provider-package catalog loading remains an explicit Host operation outside Harness.

The public Python API, Harness Observation registry, Harness state envelope, Environment provider-state codecs, Pydantic message codec, plugin contract, and model recovery rules evolve independently. The package tracks the repository-selected compatible Pydantic AI release and relies only on documented public Agent, Capability, Model, Toolset, instrumentation, message, deferred, event, output, and usage APIs.

Additional third-party provider factories, hosted adapters, managed-tool policy integrations, media/document converters, web/Web Provider clients, the OpenTelemetry SDK/exporters, and Langfuse or Logfire activation remain optional Host composition. Native Direct Local/E2B and Envd Local/Docker/HTTP/WebSocket provider support ships in the required provider package without extras, but importing either distribution initializes no SDK, subprocess, or resource. Local Envd receives one exact Host-resolved executable through its runtime collaborator; neither the provider package nor the low-level EIP client discovers or downloads it. Selecting a concrete optional Capability validates its required dependencies at construction and fails locally with a bounded configuration error; unrelated Harness imports and Agents remain usable.

Harness middleware packages may register factory classes under `a13n_harness.plugins`. Environment Providers register under `a13n_environment.providers` and are loaded only through that package's explicit catalog. Explicit catalog APIs import only selected names into immutable catalogs. An opted-in builder constructs its own Harness plugin catalog from enabled document entries; it never scans or applies an Environment catalog. Provider resolution and adapter construction remain explicit Host operations, while every other extension remains direct code-first composition. There is no process-global registration or import-time auto-enable behavior.

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
