# Tool Execution

## Design Position

Pydantic AI owns function-tool schema validation, Toolset composition, the Tool Manager, native external and approval deferral, and result integration. Its public deferred contract validates message-level call identity and completeness, but it does not validate `ExternalToolset` arguments against the declaration's JSON Schema, preserve request categories in message history, authenticate results, or prove exact remounted surface identity. Native Pydantic function tools and Toolsets run directly without adopting a Harness base class or metadata schema. Function tools that opt into Harness-managed identity, authorization, credentials, side-effect, retry, and result-safety behavior attach `HarnessToolMetadata`; one Harness-owned outer `WrapperToolset` recognizes that metadata and performs the additional preparation and dispatch checks.

Client-side tools use Pydantic AI `ToolDefinition`, `ExternalToolset`, `DeferredToolRequests.calls`, and `DeferredToolResults.calls` directly. They are model-visible schemas whose implementation and authority remain outside the Agent process. They do not pass through the function-tool invocation pipeline, execute through `a13n-envd`, or reuse approval semantics.

The built-in `UserInteractionCapability` validates and normalizes `ask_user_question` arguments before native external deferral. The request must satisfy the complete structured-question model, including non-blank text and uniqueness of question texts and option labels after whitespace normalization. Invalid requests return a bounded native tool failure to the model for correction; they never become pending Host questions. Valid requests retain native external suspension and result correlation.

The function-tool wrapper is an Agent invocation boundary, not Python isolation. An unannotated native function tool is treated like other trusted in-process plugin code: it keeps Pydantic AI semantics but is outside Harness-managed authorization and side-effect guarantees. Installing or supplying that code grants process authority, whether or not it is model-visible.

## Boundary

| Concern                                                            | Owner                                                            |
| ------------------------------------------------------------------ | ---------------------------------------------------------------- |
| Function-tool definition/validation, tool manager, deferred values | Pydantic AI                                                      |
| Optional Harness tool metadata and effective-surface resolution    | Harness                                                          |
| Managed invocation wrapper and final tool-surface recording        | Harness                                                          |
| Agent policy and credential decisions for managed tools            | Harness default plus optional fresh `InvocationPolicyCapability` |
| Optional tool risk assessment                                      | Optional review in `ToolPermissionsCapability`                   |
| Remote operation and side-effect evidence                          | Tool provider                                                    |
| Grant signing and authenticated transport                          | Host security or provider adapter                                |
| Direct I/O by trusted Python plugins                               | Plugin process trust boundary                                    |
| Client-side definition/run selection and Toolset composition       | Client Tools Capability                                          |
| Client-side model schemas, instructions, and external deferral     | Client Tools Toolset and Pydantic AI                             |
| Client-side execution, authorization, and result production        | External executor or Service client                              |
| Durable client-call waiting, delivery, and feedback correlation    | Host                                                             |

## Tool Metadata

```python
type ToolEffect = Literal[
    "read",
    "write",
    "delete",
    "execute",
    "external_communication",
]

type IdempotencySemantics = Literal[
    "none", "read_only", "provider_key"
]


class CanonicalResource(BaseModel):
    namespace: str
    kind: str
    identifier: str


class ToolOutputPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_inline_bytes: int
    max_output_bytes: int
    overflow: Literal["fail", "truncate", "spill"] = "spill"
    redact: bool = True


@runtime_checkable
class ToolResourceResolver(Protocol):
    async def __call__(
        self,
        arguments: Mapping[str, object],
        *,
        context: AgentContext,
    ) -> tuple[CanonicalResource, ...]: ...


@dataclass(frozen=True)
class HarnessToolMetadata:
    tool_id: str
    effects: frozenset[ToolEffect]
    credential_audiences: tuple[str, ...]
    idempotency: IdempotencySemantics
    output_policy: ToolOutputPolicy
    resource_resolver: ToolResourceResolver | None = None
    superseded_by_tool_ids: frozenset[str] = frozenset()
```

`HarnessToolMetadata` is stored under a reserved key in Pydantic AI `ToolDefinition.metadata`; there is no parallel tool binding object. Pydantic fields retain ownership of name, schema, kind, strictness, timeout, sequential execution, deferred loading, and the optional runtime `toolset_id`. `tool_id` is the stable policy identity before model-visible renaming or prefixing. It is unique within one prepared candidate surface: a second function-tool definition with the same ID fails before surface resolution, even when visible names differ. Intentional aliases therefore use distinct policy IDs and can share a host-owned implementation reference outside this contract.

`ToolEffect` is a small conservative policy hint, not an execution result or authorization grant. `read` observes state; `write` creates or changes state; `delete` removes state; `execute` starts code or a process; and `external_communication` sends data or a message outside the selected Environment. A tool declares every applicable value, so a shell command commonly declares `execute` plus `write`, and an HTTP-post tool commonly declares `external_communication` plus `write`. The actual provider receipt remains the evidence of what occurred. The former `create` and `update` distinction is intentionally collapsed because generic policy cannot classify upsert, patch, append, and replacement consistently before provider resolution.

`CanonicalResource` compares by the exact `(namespace, kind, identifier)` tuple after provider-aware resolution and contains no credential or display-only alias. `IdempotencySemantics` distinguishes no replay guarantee, non-mutating replay, and mutation replay under the same provider key. `ToolOutputPolicy` defines the final per-tool serialized text/JSON safety boundary, explicit overflow behavior, and whether the managed redaction pass is required. It is not the normal presentation budget for a first-party Toolset. It does not meter or rewrite native multimodal content; the definition-selected request content filter owns model compatibility, validation, and media limits at the later model-request boundary. The policy is frozen, and Toolset preparation defensively normalizes metadata so a caller cannot mutate nested policy after validation. `max_inline_bytes` must be at least 512 and no greater than `max_output_bytes`; both are narrowed by finite Harness hard ceilings that cannot be disabled by tool metadata or Host configuration. Positive bounds and non-empty identity/resource fields are validated at Toolset preparation. Harness metadata supplies only semantics absent upstream and grants no authority. The metadata and optional resolver are trusted process objects rather than durable definition state; the resolver receives the same schema-validated, type-converted argument mapping that Pydantic will dispatch plus trusted `AgentContext`, not credentials or raw model text.

Recovery retry safety is a separate optional tool metadata declaration, `a13n.harness.recovery_retry_safe`, named by `RECOVERY_RETRY_SAFE_METADATA_KEY`. `recovery_retryable()` attaches it to an individual callable or a copied native `Tool`, including tools contributed by Capabilities and Plugins. It does not opt an otherwise unmanaged tool into managed dispatch and is not derived from `IdempotencySemantics`. [Restored Pending Tool Calls](10-snapshot-and-resume.md#restored-pending-tool-calls) owns its per-run automatic recovery behavior.

`HarnessTool` is an optional thin subclass of Pydantic AI `Tool[AgentContext]` that attaches complete Harness metadata for first-party or policy-managed tools. Using that subclass is not required: any Toolset that places a structurally valid `HarnessToolMetadata` instance or mapping under the reserved key participates through duck typing. The Harness does not infer managed authorization metadata from function names, Python annotations, JSON schemas, or module origin. The separate permission identity derives only from an explicit stable ID or the source/original-name convention above; it implies no effects, credentials, resources, or idempotency. A reserved key with an invalid or incomplete value fails toolset assembly instead of silently downgrading the tool to unmanaged dispatch.

An ordinary function-tool definition without the reserved metadata remains callable and is not assigned guessed authorization metadata. Its native JSON and `ToolReturn` textual fields still cross the code-owned default truncating output boundary; arbitrary non-JSON native return values retain Pydantic semantics. This includes dynamically discovered locally executable tools from Pydantic AI `MCPToolset`; the Harness neither guesses managed metadata from MCP provenance nor adds an MCP-only result path. When `resource_resolver` is absent, managed authorization is explicitly tool- and action-level and `resources` is empty; provider-specific resource enforcement still applies. The current run's `InvocationPolicyCapability` can select a strict profile that rejects unannotated model-visible function tools. Static, dynamic, and deferred definitions are checked at their run-time Toolset preparation boundary before they can enter a model request; strictness is not a hidden builder option or immutable executable field. This is an explicit run policy rather than the base behavior.

Capability authors assign any required timeout on their owned `Tool`, `FunctionToolset`, or custom Toolset. `AgentSpec.tool_timeout` configures native Agent-level tools and does not implicitly propagate into Capability-owned Toolsets; because the Harness exposes no top-level Agent tools, a feature must not rely on that field for its dispatch deadline.

### Tool Surface Resolution

Every run-step has a **candidate tool surface** produced after Pydantic prepares all ordinary contributing Toolsets. The Harness-owned `ToolSurfaceCapability` wraps that complete candidate surface exactly once and produces the **effective tool surface** consumed by later wrappers, CodeAct, the final Tool Manager, and model requests. Feature Toolsets contribute candidates declaratively and never coordinate visibility through mutable `AgentContext` state or preparation order.

A Run with `RunBindings.deferred_tools_supported=False` cannot expose a model-visible tool whose prepared Pydantic `ToolDefinition.defer` property is true. Mandatory surface resolution uses that canonical upstream property, rather than a Harness-owned list of kinds, and removes every declaratively external or approval-gated candidate before supersession, CodeAct catalog construction, execution-boundary wrapping, and model-request assembly. Ordinary `function` tools and provider-native tools remain eligible. First-party Toolsets also omit their external-tool or approval guidance for an unsupported Run, and the first-party User Interaction Capability contributes neither instructions nor tools. These rules are invocation-scoped safeguards over a complete definition. They do not mutate definitions or infer Host support from instance lineage.

Declarative filtering cannot predict argument-sensitive or policy-sensitive deferral. If a remaining function in an unsupported Run, argument validator, Capability hook, custom Toolset, or managed invocation policy raises `CallDeferred` or `ApprovalRequired` at runtime, the mandatory outer `ToolExecutionBoundaryCapability.handle_deferred_tool_calls()` resolves every call and approval with `ToolDenied("Deferred tool interaction is unavailable for this Run.")`. Pydantic records ordinary denied tool results and continues its model loop inside the same Run. The boundary returns `None` for a supported invocation, preserving native accumulation dispatch and Host-configured deferred handlers. Because the mandatory boundary is ordered before user Capabilities and completely resolves an unsupported batch, a user `HandleDeferredToolCalls` Capability cannot bypass the current Host support setting. This denial does not permanently hide the function: deferral may depend on arguments or current policy, and repeated model attempts remain bounded by native usage limits.

For a managed function or unapproved tool, `superseded_by_tool_ids` names exact stable managed `tool_id` values whose prepared presence makes the declaring candidate redundant. Resolution performs these steps over one complete prepared candidate mapping:

1. for a Run without deferred support, omit every candidate whose prepared upstream `ToolDefinition.defer` property is true;
2. normalize every remaining candidate's reserved managed metadata and reject invalid kinds or duplicate candidate `tool_id` values;
3. collect the complete set of prepared managed tool IDs without considering authorization outcomes;
4. validate that no declaration targets its own ID and that present declarations do not form a directed supersession cycle; and
5. omit each managed candidate whose `superseded_by_tool_ids` intersects the prepared ID set, preserving every other candidate unchanged.

A target does not need to be present or known to the current executable; an absent target has no effect. Presence means that the target survived its owning Toolset's normal run-step preparation and appears in the same candidate surface. It does not mean that invocation policy will authorize the target, that an Environment currently has a ready binding, or that the target will succeed. Supersession is therefore a schema and routing simplification, not fallback selection, authorization, capability discovery, name-collision handling, or runtime health checking.

Only managed function and unapproved tools participate. External client tools, provider-native tools, output tools, and unmanaged native tools neither declare nor satisfy this relation. A candidate visible-name collision remains owned by Pydantic Toolset composition and fails independently; supersession never chooses a winner by name. Toolsets must keep any guidance required to invoke a supersedable tool in that tool's own description or schema rather than publishing unconditional standalone instructions that become false when the tool is absent.

Wrapper ordering is part of the contract:

```mermaid
flowchart LR
    Candidates[Ordinary prepared Toolsets] --> Surface[Mandatory tool-surface resolution]
    Surface --> Proxy[Optional ToolProxy discovery wrapper]
    Proxy --> CodeAct[Optional CodeAct wrapper]
    CodeAct --> Boundary[Mandatory tool-execution boundary]
    Boundary --> Manager[Final Tool Manager and model request]
```

Mandatory surface resolution wraps upstream tool search and deferred capability loading, so the `load_capability` tool and newly loaded definitions receive the same surface and execution checks. Optional ToolProxy composition follows surface resolution and precedes CodeAct. When either is absent, the next outer wrapper directly wraps the remaining inner surface. CodeAct builds and validates its catalog only from the effective surface, then adds its runner tools. The execution boundary normalizes, authorizes, bounds, and records only that final effective surface. `AgentContext` may retain the resulting managed surface snapshot for resume validation and diagnostics, but that snapshot is an output of resolution and never an input used by sibling Toolsets.

### Grouped ToolProxy Discovery

ToolProxy is an optional code-first discovery and call presentation for large collections of locally executable tools. It changes what the model sees, not who executes tools. `ToolProxyCapability(groups=..., config=...)` is the concrete-source installation entry: its mapping assigns each group name to a passive `ToolProxyGroup(source=..., description=...)` value. A source is a concrete native Toolset or Capability instance. The Capability assembles selected sources and one search/call surface through native composition; the descriptors are not independently installed Capabilities. Ungrouped tools remain directly exposed.

The Host owns configuration schemas, source factory selection, and direct-versus-grouped presentation at definition construction. `AgentDefinition.tool_proxy` and the corresponding `HarnessBuilder.build()` argument accept an immutable `ToolProxyPlan`. Its named `ToolProxySelection` values contain descriptions, references to already selected concrete Capabilities, and exact Harness plugin instance IDs. Harness orders and Agent-binds plugins, collects their contributions once with ownership retained, validates each original source, and applies the plan before native Agent construction. Unselected sources and repeated membership fail construction. There is no arbitrary graph-rewrite callback, source activation, plugin scan, or new contribution factory requirement. The concrete-source Capability and the build plan use the same grouping semantics; neither mapping is a live reconfiguration interface.

Grouping preserves native independently sortable nodes and their original contribution order, type/instance ordering references, explicit IDs, state namespaces, hooks, and instructions. Sources in one group do not become one atomic sorting node. Existing authored wrappers retain their native atomic semantics, and combined containers retain their custom instruction composition. A selected combined source overriding native behavior beyond instructions remains an atomic wrapped source so its binding and lifecycle overrides execute. Its children remain subject to native wrapper ordering constraints; grouping does not manufacture cross-boundary ordering or flatten away authored behavior. Native composition remains the only sorter and lifecycle owner. Plugin middleware remains installed even when its tools are grouped.

A group has an explicit identifier and a short domain description. Group identifiers match `[A-Za-z][A-Za-z0-9_-]{0,31}` and exclude `__`; descriptions contain non-whitespace text and at most 512 characters. Multiple Toolsets can contribute to one group only with the same description and distinct local tool names. A prepared member has the canonical name `group__local_name` before any outer native prefixing. Its `(group, local_name)` pair is its discovery identity; managed `tool_id` remains unchanged. Duplicate pairs, conflicting descriptions, nested group assignment, and collisions with configured control names fail preparation rather than choosing a winner.

Membership is passive presentation metadata. The current prepared `ToolManager` directory is the sole executable directory. Search and call derive their group index from that directory after availability, native preparation, deferred-support filtering, and managed supersession. Hidden member definitions stay registered in that manager; only their model-facing function schemas are omitted. No second client, execution registry, credential owner, or persisted discovery snapshot is introduced. Grouping does not defer source initialization or avoid provider-side tool listing: it reduces model context, not source preparation cost.

Grouped sources retain native Capability Agent/run binding before their local Toolset contributions are read. In particular, a wrapped `ContextualMCP` resolves its fresh headers and upstream replacement once per logical Run under the [MCP context-header contract](04-capability-model.md#mcp-context-headers). Grouping never captures a pre-binding authenticated client or changes native transport ownership.

#### Configuration and Dynamic Schemas

`ToolProxyConfig` is an immutable code-first value with these defaults and bounds:

| Field              | Default              | Contract                        |
| ------------------ | -------------------- | ------------------------------- |
| `search_name`      | `search_proxy_tools` | 1-64 character tool identifier  |
| `call_name`        | `call_proxy_tool`    | Different from `search_name`    |
| `max_results`      | `10`                 | Integer from 1 through 100      |
| `max_search_bytes` | `32768`              | Integer from 1024 through 32768 |

Both control names match `[A-Za-z_][A-Za-z0-9_-]{0,63}`. Their descriptions list only currently available group names and descriptions, not every member schema. Both controls and their generated Toolset instructions are absent when no grouped members survive preparation. The current group enum is prepared anew for each step; configuration names are reflected consistently in schemas, descriptions, and instructions.

Search accepts `query` (at most 2048 characters), optional `group`, optional positive `limit` bounded by `max_results`, and nonnegative `offset`. An omitted or null limit uses `min(5, max_results)`. An empty query browses; keyword search matches group names, local names, and descriptions with deterministic ordering. Search returns `tools`, `total`, and nullable `next_offset`. Each result includes the exact group and local name, current description, full `parameters_json_schema`, optional `return_schema`, effective `codeact_eligible`, and source Toolset `instructions`. Discovery invokes no business tool and grants no execution authority.

The complete UTF-8 JSON search result fits `max_search_bytes`. Pagination drops whole entries rather than truncating schemas or instructions. If even the first matching entry cannot fit, search fails explicitly and directs the Host to expose that tool directly or reduce its schema. Offsets and discovered schemas are step-local observations, not stable cursors or durable execution promises; a removed tool cannot be invoked through stale discovery. Source Toolset guidance is returned on discovery instead of being eagerly included for every group. Disabling Toolset instructions suppresses both generated guidance and discovered source instructions; separately authored Capability instructions retain native behavior.

Call accepts exactly `group`, `tool`, and an `arguments` object. The envelope intentionally does not combine all member parameter schemas. The model discovers an exact schema, then supplies matching arguments; it can reuse a known current schema without another search. The original prepared validator remains authoritative, including native conversions and custom validation hooks.

#### Native Dispatch and Failures

An ordinary model proxy call resolves the envelope, creates a fresh nested call identity, and calls the same `ToolManager.handle_call()` used by native execution. The original target retains its prepared Toolset, managed policy identity, wrappers, validation/execution hooks, approvals, credentials, timeout, return integration, and native retry state. Retry messages are correlated to the outer model call without also spending that envelope's retry allowance for target errors. The proxy never invokes an underlying Python function or MCP transport directly and never retries side effects itself.

Search consumes its normal successful tool call. An ordinary successful proxy invocation consumes two native successful calls: the outer envelope and its target. Available parent usage is checked before dispatch for both. The call control is conservatively sequential, preserving a barrier even when different targets have different sequential policies. Grouping does not imply parallel execution or transactionality. [CodeAct](18-codeact.md#toolproxy-composition) resolves the same envelope at its existing bridge and counts the actual target rather than executing a redundant proxy envelope.

Local function and approval-gated tools are supported. External tools, provider-native tools, output/control tools, CodeAct runners, and deferred-loading definitions are rejected, including unsupported flags added by outer preparation wrappers. Native `ToolSearch` can coexist for separate ungrouped tools; the default search names do not collide. MCP grouping requires local execution with `native=False, local=True`, not provider-native fallback.

Inline native approval handlers retain their behavior. Denial or unresolved `ApprovalRequired`/`CallDeferred` becomes an explicit failed proxy result; a nested proxy call never establishes a cross-turn continuation. Tools that require external execution or unresolved Host interaction must be exposed directly. Cancellation propagates normally. Unexpected target exceptions expose only a bounded failure category and warn that effects may be uncertain; callers inspect external state before retrying mutations. ToolProxy neither rolls back completed work nor promises recovery of interrupted nested calls.

### Passive Tool Runtime Metadata

A Toolset may define a public `ToolMetadataKey[T]` and immutable value class when another built-in or external Capability needs to contribute passive run-specific interpretation. Capabilities publish those values through `AgentContext.tool_metadata`; the owning Toolset reads them and remains solely responsible for matching, validation, conflict semantics beyond owner identity, and clamping to implementation hard limits. Keys accept any external runtime class and require no Harness registration. Publication is typed with `isinstance`, owner-bound, idempotent for the same value, and reused across all internal `ModelAttempt` values of the logical run.

This registry is not Pydantic `ToolDefinition.metadata`, which describes one assembled tool to the execution boundary. It does not add a Capability lifecycle, ordering graph, tool lookup, dispatch path, callable hook, authorization grant, provider client, or portable state channel. A feature that needs active behavior or another live collaborator still uses a Capability, Toolset, fixed `AgentContext` field, or typed plugin/run binding as appropriate.

The Harness always installs exactly one code-owned `ToolExecutionBoundaryCapability`. Its `get_wrapper_toolset()` contribution is a `ToolExecutionBoundaryToolset` ordered outermost around the effective non-output Toolset after per-step preparation, mandatory surface resolution, and optional ToolProxy and CodeAct composition; Host code cannot replace its preparation, dispatch, or textual-result algorithm. It declares `CapabilityOrdering(position="outermost", wraps=(AbstractCapability,))`, so it sorts before AgentSpec, definition, plugin, run, instrumentation, and other same-tier Capabilities regardless of contribution order. An incompatible Capability that attempts to wrap this boundary creates an ordering cycle and fails run setup before tool exposure rather than weakening the boundary. The wrapper therefore sees function, unapproved, and external definitions, branches on `ToolDefinition.kind`, and never intercepts output or provider-native tools. It normalizes managed function metadata, enforces final client-tool names, and delegates ordinary external deferral to Pydantic without calling an external function body.

A run supplies at most one fresh `InvocationPolicyCapability` through `RunBindings.capabilities`; feature-specific setup and dispatch require its documented public type and stable Capability ID and reject incompatible values before model exposure. The optional policy Capability can retain a typed evaluator, credential broker, and invocation-grant broker, but those grant nothing without the current run's Identity and invocation context. When no explicit policy is supplied, managed metadata-aware tools use the code-owned default allow decision with no dispatch retries, while credentials, approvals, grants, and Provider authorization remain independently required when applicable. An embedded caller that enables managed Environment tools therefore needs no local allow-policy boilerplate; the live `BoundEnvironment` still narrows every call through captured Identity, effective mount actions, readiness, and Provider policy. A supplied policy can only narrow or condition this default. There is no specialized local-policy Capability, class-free role registry, serialized component bundle, or model-authored ID that can create broader authority.

Known `EnvironmentError` failures during managed resource preparation use the same public operation-error projection as dispatched first-party tools: a bounded, redacted failure with the public error code, code-owned safe message, optional retry hint, and safe structured details. They emit `preparation_failed` and do not proceed to policy evaluation, credential acquisition, or dispatch. Provider exception text and arbitrary details are not exposed. Unexpected resolver exceptions retain the generic preparation-failure message. Multi-path tools still resolve every endpoint before any mutation; an invalid endpoint never permits partial dispatch.

### Tool Permissions and Review

Every locally executable function or unapproved tool has one trusted `ToolIdentity` and passes the same permission gate, including ordinary Pydantic tools, local MCP tools, and targets reached through ToolProxy or CodeAct. This gate is independent from the richer managed invocation policy. External client tools, output tools, and provider-native tools retain their separate authority boundaries. External declarations can be denied before deferral, but local `ask` and `review` modes are unsupported and fail preparation instead of inventing local approval for client execution.

Managed tools retain `HarnessToolMetadata.tool_id`. Other tools use `tool/<source-id>/<original-name>` or `mcp/<source-id>/<original-name>`, with each source/name segment percent-encoded. MCP sources require an explicit stable source ID; ordinary sources without an ID use `native`. Native prefix/rename wrappers preserve identity. Hosts apply `ToolIdentityToolset` before custom presentation wrappers that cannot preserve original identity themselves. Explicit identity metadata is trusted code, not model input; duplicate effective identities fail preparation rather than selecting an arbitrary tool.

`ToolPermissionsCapability` selects a frozen `ToolPermissions(default="inherit", rules={})`. Rules match exact IDs first, then the longest namespace prefix ending in `.*` or `/*`, then `*`, then `default`. `inherit` resolves the tool's declared default; it is not an execution decision. All tools default to `allow`, without review; external and provider-native tools retain separate authority boundaries. A trusted explicit tool default or permission rule can opt a locally executable tool into `review`. Installing a reviewer or defining risk rules alone never enables review. Effective modes are:

| Mode     | Behavior                                                                                 |
| -------- | ---------------------------------------------------------------------------------------- |
| `allow`  | Continue without front-gate review or permission approval.                               |
| `deny`   | Return a native tool failure before custom validation, resource resolution, or dispatch. |
| `ask`    | Require a native structured approval for the current call.                               |
| `review` | Invoke the selected reviewer; if none matches, continue without an added restriction.    |

The order is native structural validation and conversion, permission/review, tool-owned argument validation, managed resource resolution and current invocation policy where present, credentials/grants, then dispatch. A front permission denial incurs no review cost. An invocation-policy denial may occur after review because that policy needs resolved resources. No mode or reviewer outcome bypasses current tool, Host, Environment, or Provider authority.

`ToolPermissionsCapability` owns permission selection and optional review for all local tools in one definition-selected Capability. There is no independent reviewer Capability registration or lookup. Its `review` configuration accepts `ToolReviewConfig`; code-first construction also supports a default `ToolReviewer`, selector-specific reviewer implementations with the same specificity rules, and `ToolReviewPolicy` for custom reviewers. The same Capability binds reviewers to each Run, resolves the auxiliary Model, and executes bounded review. Shell commands share the risk model, policy, history, and execution gate; only input rendering and relevant review criteria specialize shell behavior. The conceptual review values are:

```python
class ToolReviewRequest:
    tool_id: str
    tool_call_id: str
    tool_name: str
    description: str | None
    parameters_schema: JsonObject
    arguments: JsonObject
    task: str | None
    context: JsonObject
    omitted: tuple[str, ...]
    profile: Literal["shell", "general"]
    approved: bool
    previous_reviews: tuple[ReviewEvidence, ...]
    recent_actions: tuple[ReviewEvidence, ...]


class ToolReviewAssessment:
    risk: ToolRiskLevel  # low < medium < high < extra_high
    reason: str | None = None


class ToolReviewResult:
    assessment: ToolReviewAssessment
    usage: tuple[ProviderUsage, ...]


class ToolReviewRule:
    risk_threshold: ToolRiskLevel | None = None
    on_flagged: Literal["deny", "approval_required"] | None = None


class ToolReviewPolicy:
    risk_threshold: ToolRiskLevel = "extra_high"
    on_flagged: Literal["deny", "approval_required"] = "deny"
    rules: dict[str, ToolReviewRule] = {}
```

The reviewer returns risk and an optional reason, never an authorization decision. An absent or null reason means no textual explanation is available; a supplied reason remains nonblank, bounded text without NUL. Missing explanation does not invalidate a risk assessment, change policy, or cause a synthetic explanation. Existing assessments with a reason remain valid. The runtime independently computes `allow`, `deny`, or `approval_required`: at or above the threshold it applies `on_flagged`; below it review adds no restriction. Policy chooses one best rule: exact tool ID, longest `.*` or `/*` prefix, then `*`. Omitted fields inherit the global fields, not a less-specific rule. Permission modes remain independent: explicit `allow`, `deny`, or `ask` does not run review. `ToolReviewConfig` extends the policy with Model, instructions, settings, reviewer deadline, and error handling. Code-first custom reviewers can instead supply `ToolReviewPolicy`; supplying both config and policy is invalid.

### Review Context and History

`ToolReviewer.review(request, *, context: AgentContext)` returns `ToolReviewResult`. Requests project converted arguments and the original parameter schema, not canonical resources or live clients. Environment variable values are omitted. Input includes the latest task/correction text (at most two string user prompts, 2048 characters each), a bounded description, and a passive snapshot of at most 16 Environment mounts with provider types, roots, working directories, and operation ceilings. Constructing context performs no Environment I/O. Business system prompts, AGENTS guidance, model thinking, and raw conversation history are not inherited by the model reviewer.

The XML renderer escapes untrusted values and measures UTF-8 size after escaping. It targets 16 KiB and never exceeds 64 KiB. Current arguments and their parameter schema remain intact after redaction; optional history, description, task, and Environment blocks can be omitted as whole blocks with explicit omission markers. If required current-call input alone exceeds the hard bound, review fails with `tool_review_input_too_large` rather than silently truncating the operation. The same rendered-input bound is checked for custom reviewers. A shell launch (`environment.shell_exec`) renders command and execution context separately; other tools use structured argument fields.

Review history is compact advisory evidence in existing portable `HarnessState`, retained when the Host saves and restores that state across approval resumes and later Runs. At most 48 flat records survive; there is no recursive review context, full argument/result archive, or independent approval store. A request selects at most five relevant previous reviews and eight recent actions. Records carry tool/call correlation, a bounded redacted target and optional reason, risk where available, the effective policy decision, native approval or denial observations, and an execution observation. The current native call-local approval decision is separate from historical evidence.

Observed dispatch starts as `unknown` and becomes `tool_returned` or `tool_reported_failure` only when the local tool returns. A returned value is not proof that an external effect succeeded; exceptions and cancellation leave the outcome unknown. Reviews and confirmed human denials are `not_executed`. Actual root human denials are recorded from native typed results, with optional pending-request display metadata, including inline and resumed paths; response text or model-supplied metadata never creates approval facts. Nested ToolProxy and CodeAct local calls use the same history boundary. Concurrent record updates do not lose evidence; no history lock is held across model or tool I/O.

History does not grant permission, lower a risk threshold, automatically reuse a review, or prove execution. Fresh review runs again after approval resume; a current denial wins over earlier approval. A missing matching reviewer adds no restriction and emits no assessment. This skips only optional review; tool-owned validation, managed invocation policy, and Environment authority still apply. Failures from a selected reviewer retain the error and timeout behavior below.

### Model-backed Review and Shell Specialization

Model-backed review uses the selected logical Model through the Host's normal resolver and authentication path, including native TypeSafe Jev Models. It has no business tools and permits only one bounded model request. Dependencies contain only the projected `ToolReviewRequest`. Shared criteria define general and shell risk levels and historical-evidence boundaries. Configured `instruction` remains a separate escaped `<custom-instruction>` block; `shell_instruction` overrides it for the shell profile.

Text-capable Models return structured risk and an optional reason. Models declaring no text output instead receive a described four-level severity rubric: 0 maps to `low`, 1 to `medium`, 2 to `high`, and 3 to `extra_high`. The native adapter's rubric level determines risk; confidence and unrounded provider scores do not change policy. The resulting assessment has a null reason. Questions and assessment criteria are supplied as instructions and schema descriptions, separately from the rendered request material. The output schema contains no free-text field for non-text Models, and provider compatibility wrappers do not enable text output on them. Plain text, including JSON text, is never an assessment. No additional explanatory Model call, implicit fallback, or confidence threshold is added.

Invalid output and non-timeout failures follow `on_error` (`approval_required` by default, or `deny` or explicit `allow`). The review deadline defaults to and is capped at 120 seconds, including custom reviewers. Reviewer timeout always denies before dispatch regardless of `on_error` or earlier approval. Cancellation propagates. `ToolReviewError` can retain proven usage without exposing raw provider errors. Human interaction timeout belongs to the Host, not the reviewer or tool profile. [Events and Usage](12-events-observability-and-usage.md#tool-review-results) owns risk/result and independent effective-decision observations and accounting.

Pydantic output tools remain part of output validation, not general side-effect dispatch, and provider-native server-side tools remain model/provider configuration. A deployment that needs Harness invocation policy for a provider-native operation exposes a metadata-aware function-tool adapter instead of pretending the function wrapper intercepts provider-internal execution.

Pydantic Toolset composition owns collision handling and final model-visible names. Dynamic discovery changes visibility, not registration or authorization. First-party Environment and remote-provider tools attach Harness metadata because the platform claims identity and policy enforcement for those surfaces.

## Client-Side External Tools

Client-side tools are declared by a concrete first-party `ClientToolsCapability` in `AgentDefinition.capabilities`. It is a code-first Harness Capability rather than a custom `AgentSpec.capabilities` serialization type, so it is never present in the declarative custom-type catalog and requires no class-name resolution. The Capability's portable argument model is conceptually:

```python
class ClientToolDefinition(BaseModel):
    name: str
    description: str
    parameters_json_schema: Mapping[str, JsonValue]
    instruction: str | None = None
    metadata: Mapping[str, JsonValue] = Field(default_factory=dict)


class ClientToolsetDefinition(BaseModel):
    toolset_id: str
    tools: tuple[ClientToolDefinition, ...]


class ClientToolsSpec(BaseModel):
    default_toolsets: tuple[ClientToolsetDefinition, ...] = ()
    allow_run_override: bool = False

```

`RunBindings.client_toolsets: tuple[ClientToolsetDefinition, ...] | None` is a typed optional override; it contributes no independent tools or authority. The definition-selected `ClientToolsCapability` reads the field, applies the replacement policy below, and contributes the resulting native Toolsets. `None` retains the defaults; an empty tuple explicitly clears them. Binding construction validates and copies the declarations.

The declaration codec is not a second executable Toolset API. For each run, the Capability resolves the complete effective declaration list and composes one Client Tools Toolset. That Toolset converts each effective definition one-to-one into an upstream `ToolDefinition`, groups it in an upstream `ExternalToolset(id=toolset_id)`, and supplies optional bounded native Pydantic instruction parts. The declared name is the required final model-visible name; the outer Harness wrapper rejects another wrapper's attempted prefix or rename of a marked client definition instead of silently changing Host correlation.

A client declaration's `permission` sets its canonical source identity's default: `allow` and `deny` retain those meanings, and `inherit` uses the existing `allow` default. The configured Agent `ToolPermissions` rules/default remain the sole precedence owner and may explicitly override this declaration default. It is not an additional authority ceiling. Effective `ask` or `review` is unsupported for external tools. Client metadata cannot inject the trusted tool identity, including through nested values; native materialization supplies it from the stable toolset ID and original declared name.

Toolset IDs and model-visible names are unique in the effective client surface. The Harness validates bounded JSON Schema objects with `type="object"`, while upstream `ExternalToolset` deliberately uses an unconstrained local validator and treats that schema as model-facing guidance. Descriptions, instructions, and metadata are bounded JSON-safe content. Final collision detection remains Pydantic-owned across the complete assembled surface, including native, Capability, MCP, Environment, external, discovered, and output tools. Client metadata is non-authoritative public data. It cannot contain a credential, invocation grant, policy claim, server-only correlation value, or reserved `HarnessToolMetadata`; external tools never masquerade as Harness-managed function tools.

For each run, `RunBindings.client_toolsets` has deterministic whole-list semantics:

| Definition and run attachment                      | Effective client surface                                                        |
| -------------------------------------------------- | ------------------------------------------------------------------------------- |
| Client Tools Capability absent; attachment absent  | No client tools                                                                 |
| Client Tools Capability absent; attachment present | Run setup fails                                                                 |
| Capability present; attachment absent              | `default_toolsets`                                                              |
| Attachment present; `allow_run_override=false`     | Run setup fails                                                                 |
| Attachment present; `allow_run_override=true`      | Attachment toolsets replace the complete default list; an empty tuple clears it |
| Incompatible typed declarations                    | Run setup fails                                                                 |

The run attachment is trusted Host input for one run, but its descriptions, schemas, instructions, and metadata remain untrusted model content. It carries no Python handler, client credential, connection, callback, or side-effect authority. The effective surface is fixed before the first model request and cannot change through enqueue or Environment mount-set mutations. A child receives no client surface from its parent unless the child definition independently enables the Capability and the Host supplies the child's fresh typed run attachment.

When the effective list is non-empty, the Client Tools Capability contributes the Client Tools Toolset through native Pydantic composition; the Toolset contributes the resulting `ExternalToolset` values and declaration-owned instruction parts. Every built Harness Agent includes `DeferredToolRequests` beside its fixed business output in the construction-time Pydantic output contract; runs do not widen or override it. Pydantic marks every external definition `kind="external"` and places selected calls in `DeferredToolRequests.calls` without invoking a function body. The Harness does not recreate `CallDeferred`, an external-tool dispatcher, a prompt language, or a client callback protocol. The invocation-policy strict profile applies to function tools; it neither converts an external tool into a managed function tool nor authorizes its external side effect.

For an invocation with deferred support, the resulting `HarnessRunResult` is suspended with `suspend_reason="deferred"`, complete `DeferredToolRequests`, and `HarnessState`. Ordinary tool-call and deferred events are observations only. The terminal result and any Host-accepted durable record determine whether external execution may proceed. Supported child invocations use the same native boundary. Child execution modes and Host feedback ownership follow [Delegation and Subagents](11-delegation-and-subagents.md#host-owned-deferred-support). Runtime `CallDeferred` from a function retains its stable tool identity in request metadata; resume requires that same function identity rather than treating it as a declaratively external tool.

Resume is a new run. The Host supplies the prior state, the exact effective client surface that produced the pending calls, fresh `RunBindings`, and a `DeferredToolResume` containing the authoritative terminal `DeferredToolRequests` plus its `DeferredToolResults`. Before model or tool work, the Harness rejects duplicate or category-overlapping pending IDs, unknown, wrong-kind, already completed, incomplete, or message-mismatched results and verifies each pending external name against the current assembled surface. One negative closure is permitted: a correlated external request with no runtime-deferred function identity may receive an actual typed `ToolFailed` when its name is absent from that surface. This records a failed result without execution; it accepts neither successful values nor present-but-changed kinds or function identities. Retained-fact recovery uses the same rule. It does not relax approval validation or the Host's frozen-surface obligation for control feedback. Calling `DeferredToolRequests.build_results(...)` is the normal construction path but does not replace this preflight because native result objects remain directly constructible and partial.

Exact surface identity is a Host obligation. Pydantic message history retains call identity and arguments, not the complete prior schema, instruction, metadata, category, or toolset declaration, and `HarnessState` intentionally excludes the client attachment. The Harness detects request/result category and current assembled-name/kind inconsistencies from the supplied authoritative pending value, but cannot prove that a same-named remounted declaration is byte-for-byte or semantically identical. A durable Host compares its frozen attachment or digest before calling the Harness; an embedded Host that wants this guarantee retains and compares the same value itself.

Argument schemas guide the model but do not authorize a client action. Because upstream `ExternalToolset` does not execute the function body, the external executor validates the received arguments against the accepted schema before any side effect and applies its own authentication, user confirmation, timeout, audit, and rollback policy. The Harness never claims that an external result proves how the client produced it.

a13n Service durability, delivery, and the answers that resume the exact waiting run remain Host-owned as summarized by [Service runs](../a13n-service/05-runs.md). Embedded Hosts can implement the same stop-and-resume contract without a service.

## Invocation Context

```python
class ToolInvocationContext(BaseModel):
    invocation_id: str
    tool_call_id: str
    run_id: str
    instance: AgentInstanceContext
    tool_id: str
    toolset_id: str | None
    tool_name: str
    normalized_arguments: Mapping[str, JsonValue]
    arguments_digest: str
    resources: tuple[CanonicalResource, ...]
    idempotency_key: str | None
    deadline: datetime | None
```

`tool_call_id` follows Pydantic AI messages. `invocation_id` identifies one provider dispatch and receipt. `tool_id` comes from validated Harness metadata; `toolset_id` and `tool_name` retain the assembled Pydantic location and visible name for diagnostics. Agent Identity comes from `instance`; model arguments cannot replace any of these values. The context is derived inside the authorization Capability and is not accepted as model input.

## Execution Pipeline

```mermaid
sequenceDiagram
    participant PAI as Pydantic AI Tool Manager
    participant Wrapper as Harness WrapperToolset
    participant Policy as Selected or default policy
    participant Broker as Credential Broker
    participant Provider

    PAI->>Wrapper: structurally validated tool call
    Wrapper->>Wrapper: stable identity and permission/review gate
    Note over PAI,Wrapper: Deny or suspend before custom validation and resource resolution
    Wrapper->>Wrapper: tool-owned argument validation
    alt Harness metadata absent
        Wrapper->>Provider: native Pydantic tool dispatch
        Provider-->>Wrapper: native result or failure
        Wrapper->>Wrapper: apply default text/JSON result policy
        Wrapper-->>PAI: bounded native Pydantic outcome
    else Harness metadata present
        Wrapper->>Wrapper: apply bounds and invoke optional resource resolver
        Wrapper->>Policy: tool identity, instance, effects, resources, argument digest
        Policy-->>Wrapper: deny, allow, or approval required
        alt Approval required
            Wrapper-->>PAI: ApprovalRequired
        else Allowed
            Wrapper->>Broker: request audience-bound credential handle
            Broker-->>Wrapper: handle or no credential required
            Wrapper->>Provider: dispatch with deadline and cancellation
            Provider-->>Wrapper: result and optional receipt
            Wrapper->>Wrapper: validate, redact, and bound result
            Wrapper-->>PAI: tool return or typed failure
        end
    end
```

Pydantic structural validation precedes every dispatch. Every locally executable function or unapproved tool return crosses the same outer result boundary. Managed metadata selects authorization, dispatch retry, events, strict JSON validation, and a per-tool `ToolOutputPolicy`; metadata-absent tools receive the finite code-owned default truncation policy for native JSON values and textual/JSON fields of `ToolReturn`, while arbitrary non-JSON return objects remain native. For a managed tool, the wrapper creates a bounded Pydantic JSON projection of the type-converted arguments for policy, digest, events, and state; a value that cannot be projected fails before authorization. Harness declaration-level bounds and the optional resource resolver then precede policy and provider work. The resolver sees the typed arguments, while `ToolInvocationContext.normalized_arguments` contains only their safe JSON projection. Resolver failure stops before authorization or dispatch. Providers remain responsible for canonicalization that depends on remote state; a central policy that requires such a canonical value uses an explicit provider resolution operation before its final decision. An unmanaged tool receives no implied Harness authorization, credential broker, idempotency, semantic retry, or invocation event; the shared permission/review gate and mandatory default text/JSON result boundary still apply.

When no fresh `InvocationPolicyCapability` is supplied, managed invocations use a Harness-owned allow decision with no dispatch retries. This default makes first-party managed tools usable out of the box; it does not supply credentials, approvals, grants, or Provider authority. A fresh explicit policy can only narrow or condition dispatch through deny, approval, credential, grant, strict-surface, or retry decisions. Provider and Environment enforcement still run for every dispatched operation.

Current arguments, policy, and resources are evaluated again on resume. Credentials resolve only after validation, authorization, and approval, immediately before dispatch. Live deny and cancellation are checked at that boundary.

Supplemental `ToolReturn.content` is model context, never authored input. At native settlement, Harness lowers the original execution value followed by supplemental native values into one multimodal `ToolReturnPart.content`. Only native `CachePoint` request-control markers may remain in the supplemental field so adapters retain their cache semantics; markers are model-only control, never authored input or tool data. `ToolReturnPart.metadata['a13n.tool-content']` explicitly records `result_index` and ordered supplemental item annotations. Readable result projections use that execution-value boundary, never an unmarked first-array-item guess; media payloads are not serialized into presentation events. Supplemental text application references remain annotations, and media `vendor_metadata` remains provider options only.

The same settlement applies to ordinary tools, results replaced by Capability after-execution hooks, accepted `DeferredToolResume`, native inline deferred results, and the outer CodeAct runner. Nested CodeAct calls retain the original scalar, dictionary, or list execution value for Python evaluation; their supplements accumulate only for the outer native settlement. Genuine steering remains independent native user input. Renderers do not infer provenance from role, neighboring results, equal text, or media bytes. Previously unmarked imported history is not guessed or rewritten; callers supplying application media metadata migrate to the Harness-owned annotation boundary.

## Authorization and Grants

In-process tools are trusted code. The wrapper governs metadata-aware calls made through the assembled tool surface but cannot prevent installed code, including an unmanaged native tool, from using Python libraries, local files, or captured clients directly.

Out-of-process operations can receive an opaque reference to a host-issued invocation grant:

```python
class InvocationGrantRef(BaseModel):
    grant_id: str
    audience: str
    claims_digest: str
    expires_at: datetime
```

The host security adapter defines claims, signing, authenticated transport, and verification. The harness binds the request to the current instance, canonical action/resource inputs, argument digest, approval reference, and constraints before asking for the grant. Provider transport credentials, invocation grants, and business credentials remain distinct.

## Approval and Deferred Calls

`AgentContext.tool_approval` is a read-only call-local accessor returning `ToolApprovalContext(tool_id, tool_call_id, approved)`, or `None` outside the current tool invocation. `approved` reflects the native structured decision for this call. Concurrent calls and nested dispatch cannot borrow each other's context. Automatic `allow` decisions and review assessments do not create approval.

The Host owns legal message history and structured approval results. It may construct or edit that history without private Harness provenance, original schema/argument fingerprints, resource revisions, or historical approval verification. A native approval satisfies permission, review, managed-policy, and tool confirmation for that call; there are no source-specific approval stages. Presentation metadata is advisory, including metadata retained from older Runs. Model prose and external-call result text cannot create approval. Native denial prevents dispatch; fresh permission or policy denial and review timeout still win after approval.

Approval uses Pydantic AI `ApprovalRequired`, `DeferredToolRequests.approvals`, `DeferredToolResults.approvals`, `ToolApproved`, and `ToolDenied` directly. Client-side execution uses the separate `DeferredToolRequests.calls` and `DeferredToolResults.calls` collections. A result for one category is invalid for the other. Supported Runs preserve those native suspension and handler semantics, independently of lineage. Runs with `deferred_tools_supported=False` map every dynamically requested approval or external call to a category-preserving `ToolDenied` result inside the same Run.

Deferred-resume validation correlates pending calls with the latest response and its trailing results, not completed calls from earlier responses. Tool-call IDs need not be unique across the whole Thread. The pending batch must match the current prepared tool surface before integration; after consumption, later model steps may prepare a different surface without being constrained by the old envelope.

Pending requests must match their corresponding calls in the latest legal message history, including call IDs, tool names, and arguments. Host edits must keep those structures consistent. Native argument overrides pass through current schema validation, optional resource resolution, policy, and credential resolution again. Current tool existence and category remain required; historical managed identity attestations are not.

Approval does not reserve a credential or override a current deny. Environment resources are current observations, not reserved execution targets. Standard Environment dispatch selects its current route after invocation waits; exact-target Host policy must be enforced on the execution path rather than inferred from canonical metadata. Execution-local scopes and handles retain their own identity independently of approval. Current definition, argument, resource, or Environment constraints can still prevent execution. An approved function tool later executes under fresh server authority; an external client tool never becomes server-executable merely because another deferred entry was approved.

### Structured User Questions

Structured user interaction is a narrow client-side deferred tool rather than a live callback into the Host. Its Capability contributes a bounded question contract and answer schema, while Pydantic deferred-call identity and the Harness resume envelope provide correlation. The Host owns presentation, authenticated answer collection, persistence, expiry, and delivery. Before Pydantic integration, the User Interaction Capability validates the untrusted answer value against the exact pending question schema; generic deferred-resume preflight continues to validate message identity, category, call coverage, and the current external-tool surface.

A question suspends the originating run and therefore retains no task, socket, credential, or user-interface handle in the Harness process. A Host can report an unanswered, expired, or otherwise failed external call with native `ToolFailed` in `DeferredToolResults.calls`; its message is validated as JSON-safe, and it is not parsed as a successful question answer. Correlation, exact batch coverage, and fresh-binding checks remain unchanged. `ToolDenied` belongs to approval results, not external-call result payloads. An answer is untrusted user-supplied data carried in a correlated `DeferredToolResults.calls` value, not `RunInputValue` or ordinary user-message content. It cannot serve as approval, Identity, policy, or Environment authority merely because it arrived through the correlated question surface.

## Discovery, Proxying, and Programmatic Dispatch

Pydantic AI deferred Capability loading and tool-search primitives are the default progressive-disclosure path. Loaded Capability IDs and discovered tool names use upstream message-derived state and are persisted only when their continuation semantics require it.

A fixed-catalog proxy Capability remains available for deployments that expose a stable search/execute pair, cache remote definitions, or hide provider tools from direct model invocation. It preserves the underlying tool identity and any Harness metadata; discovery or proxy ownership never upgrades an unmanaged tool into a managed one or bypasses authorization for a managed tool.

Programmatic dispatch uses Pydantic AI `RunContext.tool_manager`. It crosses the same prepared definition, validation, Capability hooks, managed-or-native dispatch choice, owning Toolset, event, and successful-call accounting path as model-selected calls. A proxy-only grant is scoped to its owning wrapper and target managed tool and is not a general host-call bypass.

The optional restricted Python orchestration surface is owned by [Restricted CodeAct Orchestration](18-codeact.md). CodeAct adds typed deny-by-default eligibility, run-local Monty sessions, bounded nested dispatch, and inline/file runner semantics without creating another tool router or widening the authority defined here.

## Credentials

The broker returns an opaque, audience-bound lease or injection handle. Credential material is absent from prompts, model arguments, results, `HarnessState`, events, traces, and normal logs. Redirects do not forward it to another audience, and failure does not fall back to ambient host credentials.

Environment-variable projection is an explicit compatibility mode for a specific dispatch adapter, not a default tool behavior.

## Dispatch, Retry, and Results

Local tools execute through the Pydantic AI toolset interface. Remote adapters receive validated input, the optional grant reference, a credential handle, cancellation, and deadline.

Read and mutation retry behavior comes from declared provider semantics. A mutation repeats only when the provider supports the same idempotency key and semantic request. Timeout, cancellation, or transport loss after dispatch is unknown without provider evidence and is reconciled rather than assumed failed. A non-cancelled run receives a bounded safe `unknown_outcome` tool failure plus an `invocation` observation. When external task cancellation must propagate, the wrapper emits the same bounded observation before re-raising when the dispatch boundary is known, but that event remains best-effort process-local telemetry rather than a durable receipt. Provider receipts or a Host-owned dispatch/idempotency ledger are the durable reconciliation authority; cancellation and event loss never cause `HarnessState` to fabricate one.

Parallel model tool calls do not imply safe parallel effects. Effect metadata, semantic resources, and provider limits determine concurrency.

Managed-tool results pass through declared output validation, text/JSON redaction, final size policy, optional controlled spill, and event emission. Metadata-absent function results receive the same textual/JSON redaction and finite default final policy with `overflow="truncate"`, but no managed identity, authorization, credential, retry, invocation event, or spill file is fabricated. Truncation and managed spill failure remain explicit. Effective limits are the minimum of implementation hard ceilings, Host runtime ceilings, provider-advertised limits, and the selected per-tool or code-owned default policy; no layer can widen an upstream ceiling. Provider-native tools do not execute through this local function-tool boundary; their provider request and result integration remain upstream behavior.

First-party Toolsets own normal model-facing progressive disclosure because only the semantic owner knows whether to page complete entries, preserve whole lines, retain a continuous process-output prefix, keep log head and tail, or direct the model to a narrower follow-up operation. A shared public Toolset helper supplies strict serialized JSON character measurement, redacted best-effort run-private file writing, exact field/collection fitting, consistent guidance, and a typed disclosure value. Its ordinary semantic target is 12,000 serialized JSON characters; UTF-8 byte ceilings remain separate transport, provider, and spill constraints.

```python
class ToolOutputDisclosure(TypedDict):
    truncated: Literal[True]
    output_chars: int
    output_bytes: int
    output_file_path: str | None
    content_complete: bool
    hint: str
```

`output_chars` and `output_bytes` measure the same strict serialized fullest-available redacted value as Python Unicode characters and UTF-8 bytes, respectively. `content_complete` states whether that value covers the complete producing operation, not merely whether the temporary write succeeded. `output_file_path` names a model-readable file containing that fullest-available value or is `None` when writing was unavailable. Tools with a reliable continuation, such as file pages or retained process output, can prefer that continuation and omit a file. Named Toolset results include `disclosure` only when their ordinary inline projection omits additional content. They do not return a universal model-visible result wrapper.

The helper returns an ordinary JSON mapping carrying a Harness-owned process-local acknowledgement type. This acknowledgement is not serialized as a flag, grants no authority, and cannot disable validation, redaction, or the finite hard ceiling. It only tells the outer boundary not to replace an already bounded semantic result at the smaller generic inline byte threshold. Acknowledged output remains shape-preserving only while it is at most 20,000 serialized characters and within `max_output_bytes`; otherwise the final safety ceiling returns the bounded generic preview regardless of the ordinary unacknowledged overflow mode. Arbitrary booleans or model-controlled fields never create the acknowledgement.

The tool execution boundary remains the sole mandatory final result boundary. After redaction, it measures both serialized characters and UTF-8 bytes for an ordinary JSON result and the textual/JSON fields of a native `ToolReturn`. Model-visible text/JSON never exceeds the code-owned 20,000-character hard ceiling, and a narrower `max_inline_bytes` or applicable `max_output_bytes` still applies. Native `BinaryContent`, URL media, `UploadedFile`, `CachePoint`, and other non-text content parts are preserved as native values and excluded from this text/JSON policy; the content compatibility filter processes them when the resulting history is next sent to a model. The result boundary does not recursively rebuild, inspect, redact, upload, or meter those multimodal parts.

An unacknowledged textual result within both the 20,000-character final ceiling and `max_inline_bytes` remains inline with its semantic shape. When it exceeds either boundary, `fail` returns a bounded tool failure, `truncate` returns an explicit bounded structure-preserving preview, and `spill` first writes the complete redacted text/JSON value when it is no larger than `max_output_bytes`. Spill eligibility and the code-owned 16 MiB proactive Toolset spill ceiling are byte policies independent of the character budgets. `RunBindings.tool_result_directory` optionally supplies a canonical absolute Environment directory for both proactive disclosure and final spill. Each spill creates a Run-private leaf below that directory; it does not require or change the default mount. When omitted, the parent remains `.a13n/tmp/tool-results` below the current default mount's root, using its explicit `mount_path` or named `/environment/{name}` compatibility root rather than the movable `/workspace` alias. Hosts supply an explicit mount path or named root when their sink must not follow default-mount changes. Every spill prepares its file selection and writes through a short scope pinned to that mount incarnation. An unavailable explicit sink never falls back to the default mount. The model receives a preview bounded by both 20,000 characters and `max_inline_bytes`, with `truncated=true`, complete serialized character and byte counts, and `output_file_path`; it reads the file later through ordinary Environment file tools. There is no model-visible retained-output selector, second Environment result projector, or cumulative budget shared across independent tool calls.

The shared spill owner registers one bounded logical-run cleanup collaborator and serves both proactive Toolset disclosure and the final fallback. Each successfully created exclusive leaf directory is recorded with its prepared selection before writing file content. Cleanup uses those original selections and removes only owned leaves, never shared parents. Default-mount changes do not retarget earlier files; unmount, replacement, changed generation, or unavailable storage can leave temporary files behind. Cleanup never reselects a replacement target to retry removal. Readiness may perform I/O, but generation fencing prevents cross-generation deletion. Spill files do not enter `HarnessState`, grant provider authority, or have a durable lifetime guarantee. Downloaded and converted user files are not spill files and are not removed by this cleanup. Returned string paths remain ordinary routes, not permanent resource handles after mount replacement. A read-only, unavailable, or topology-incompatible file sink leaves `output_file_path=null` and returns the bounded preview rather than converting an already completed side effect into a retry-shaped failure. A value beyond the caller's finite spill ceiling is never written and follows the bounded preview or failure outcome. The generic fallback preserves object/list structure by shortening string leaves when that structure fits; if structural overhead alone cannot fit, it falls back to an explicit serialized head/tail preview.

The safety contract applies while bytes are produced or incrementally read, not only after an unbounded value has been collected. First-party Environment providers capture command output into bounded memory and private spools under their own hard ceilings. Their Toolsets materialize at most their declared finite result ceiling, proactively construct a smaller semantic page or preview, preserve recoverability through a continuation or disclosure file, and release no-longer-needed provider references. Every entered provider also bounds aggregate private spool storage and never evicts a valid reference to admit another producer. These provider references remain trusted programmatic values; they are not the model-facing spill format.

`ToolOutputPolicy` remains a Harness final model-result policy and is not serialized as EIP command policy. Envd captures raw command output under its advertised finite ceiling and returns bounded previews plus explicit-offset references; the Harness adapter reads a finite page and the owning Toolset performs semantic progressive disclosure before final boundary validation. Direct Local uses its own finite raw-capture spool and the same layered contract. A Harness `fail`, Toolset spill failure, or final fallback never changes command termination or rolls back side effects after provider dispatch.

The code-owned `ToolExecutionBoundaryCapability` applies this reusable final path after every locally executable function-tool dispatch; no Filter or second result-safety Capability wraps or bypasses it. The path avoids secondary unbounded serialization, but an in-process Python function can allocate an oversized object before returning, so the wrapper cannot retroactively prevent that allocation. First-party streaming producers enforce provider hard capture bounds during production and semantic Toolset bounds before model materialization. Metadata-absent arbitrary non-JSON objects retain native Pydantic semantics and are outside textual spill; a Host requiring strict JSON results and managed authority uses the strict managed-tool profile and streaming first-party providers.

Direct provider calls and unmanaged side effects by trusted code are outside authorization, even though function-tool text/JSON returns still cross the output boundary, and remain attributable to plugin process trust.

## Failure Surface

First-party JSON tool failures use `{"ok": false, "error": {"code": ..., "message": ..., "details": {...}}}` with an optional `error.retry_hint`. `code` remains the machine contract; `message` is a bounded safe explanation and `details` is always an object. Existing domain-specific fields, such as HTTP status, byte limits, and outcome certainty, retain their meaning. Native Pydantic `ToolFailed`/`ModelRetry`, third-party tool payloads, MCP errors, and Host-owned child execution failures retain their owning protocols; this is not a universal result wrapper or exception hierarchy.

The semantic owner supplies public explanations and corrective `field`, stable `reason`, and `hint` details where known. Internal validation exposes bounded field locations and error types, not rejected input, custom validator exception text, or full validation reports. Safe Harness failures may preserve their public explanation; arbitrary provider exception descriptions are never copied into model results. Code-only provider failures receive code-owned text rather than an invented provider diagnosis. A legacy or unknown Environment code retains a safe fallback explanation and guidance, not an empty, code-only error.

Environment failures use the Provider [operation-error projection](08a-environment-providers.md#failure-semantics) in preparation, file, shell, document, and download paths. Adapters preserve original retry semantics and public effect evidence; they do not replace an Environment retry hint with a generic dependency retry. The common failure constructor retains normal tool-failure observation without copying diagnostic messages or arbitrary details into telemetry attributes. Presentation prefers an actionable hint or safe message over a bare code.

Stable harness categories cover invalid input, unavailable tool, denied, approval required, invalid external-tool declaration or result correlation, credential unavailable, provider failure, timeout, cancellation, unknown outcome, invalid result, duplicate managed identity, and internal wrapper failure. Duplicate `tool_id` values, invalid Harness metadata, invalid client declarations, and assembled name collisions fail at their owning static or per-run Toolset preparation boundary before model exposure. Provider-specific codes and protected causes remain safe extensions rather than expanding the core taxonomy.

## Trade-offs

- Reusing Pydantic AI tools avoids a second execution framework, while Harness metadata and its optional authoring helper remain additive.
- Metadata-aware wrapping makes policy consistent for managed tools without pretending to sandbox or govern arbitrary trusted plugin code.
- Opaque grant and credential references keep transport security outside model-controlled data.
- Deferred approval and client-side execution create another run boundary instead of retaining live tasks and credentials.
- Reusing `ExternalToolset` avoids the former custom client Toolset and result-correlation state machine, while durable Hosts still own authenticated delivery and feedback.

## Invariants

01. Every model-selected function-tool call resolves to one Pydantic AI `ToolDefinition`; Harness metadata is optional and never inferred.
02. Every valid Capability graph sorts the tool execution boundary outside all other wrappers; every locally executable function-tool call therefore crosses that dispatcher, which always applies the text/JSON result boundary and applies authorization, managed retry, and invocation events only when valid Harness metadata is present.
03. Managed `tool_id` values are unique within one assembled run and are checked again whenever dynamic or deferred Toolsets prepare definitions.
04. A host that requires all model-visible function tools to be managed rejects each unannotated definition at the authoritative per-run or per-step Toolset preparation boundary before model exposure; eager checks of direct static `Tool` inputs are only an optimization.
05. Approval for a managed tool binds effective input and never overrides live deny policy.
06. Managed credentials are audience-bound and never fall back to ambient authority.
07. Managed remote retry follows provider idempotency or reconciliation evidence.
08. Trusted plugin code, unmanaged tool dispatch, and direct Python I/O are not represented as wrapper-enforced isolation.
09. Every client-side tool is an upstream external tool: no handler runs in the Harness process, its declared name survives final assembly exactly, and external calls never share approval semantics.
10. A run-specific client-tool replacement is accepted only when the materialized Client Tools Capability permits it; the effective whole surface is fixed for that run and remounted exactly for deferred resume.
11. External results correlate through `DeferredToolResume` to the authoritative pending `.calls` batch and start a new run with fresh bindings; the Host verifies exact surface identity, while stream events, metadata, and client-held history grant no result authority.
12. Every locally executed function tool's native JSON or `ToolReturn` textual/JSON output is subject to finite per-call inline and total bounds; producer-side streaming or retention applies a finite capture bound before full materialization whenever the provider controls production.
13. Every first-party output spool enforces finite aggregate storage; Direct Local charges actual bytes until release or binding close, while envd charges Session-owned output until explicit release, eligible history collection, Session cleanup or daemon shutdown.
14. First-party Toolsets own semantic progressive disclosure and can share the one run-local spill owner; the mandatory outer boundary always retains validation, redaction, and a larger finite hard fallback, and no second model-visible result wrapper exists.
15. A Toolset acknowledgement is a process-local Harness type, never a model-authored flag; it bypasses only the ordinary generic inline projection and cannot bypass the final hard ceiling.
16. Native multimodal tool-return parts cross the tool execution boundary unchanged and are owned by the definition-selected content compatibility filter at model-request time.
17. The EIP adapter never serializes Harness output policy or metadata into command requests; it enforces finite provider reads over envd's bounded raw output before Toolset-owned semantic projection and final boundary validation.
18. A child surface excludes every prepared tool definition whose upstream `defer` property is true; any later runtime call or approval deferral is completely denied by the mandatory outer boundary and returned to the child model as ordinary denied tool results, while root deferred behavior remains native.
