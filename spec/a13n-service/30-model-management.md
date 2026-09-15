# Model Management

## Design Position

Service exposes two Organization- or Workspace-owned resources for primary generative-model execution:

- a `ModelProvider` is one configured account or endpoint; and
- a `Model` is one stable model alias owned by exactly one Provider, with one calling API, editable native settings, and typed Harness declarations.

Provider types and calling APIs are deployment registry values, not resources. A Workspace can create any number of Providers of the same type, such as two OpenAI accounts or two Ollama servers. Credentials, endpoint settings, region, project, and authentication mode belong to the Provider. Upstream model identity, one selected calling API, default request settings, declared input capabilities, context window, and Agent effort choices belong to the Model.

Neither resource has a version or immutable revision. Both are mutable under strong ETag preconditions. An Agent selects only `model_key` and can supply request-setting overrides. Each Run resolves the latest enabled Model when accepted and retains its execution selection and effective settings. Provider configuration is deliberately not frozen: every outbound model request resolves the Model's current Provider configuration and credential.

This contract covers only the primary text or multimodal generative model used by an Agent. Embedding, reranking, moderation, speech, image generation, video generation, and other specialized model resources are outside this domain.

## Boundaries and vocabulary

| Concept              | Meaning                                                                                      | Durable resource |
| -------------------- | -------------------------------------------------------------------------------------------- | ---------------- |
| Provider type        | Trusted implementation family such as `openai`, `openrouter`, `ollama`, or `aws_bedrock`     | No               |
| Model Provider       | One Organization- or Workspace-owned configured account or endpoint                          | Yes              |
| Calling API          | Request/response contract such as `openai.responses` or `anthropic.messages`                 | Registry key     |
| Model                | Stable scoped alias for one upstream model under one Provider                                | Yes              |
| Model settings       | Serializable native request defaults for the Model's selected calling API                    | Model value      |
| Model declarations   | User-confirmed Harness facts and Agent effort choices stored separately from native settings | Model value      |
| Model candidate      | Advisory catalog facts and suggested configuration for an upstream model                     | No               |
| Provider integration | Trusted connection, authentication, endpoint, and discovery code selected by Provider type   | No               |
| Calling API binding  | Trusted mapping from one calling-API key to one native Pydantic AI Model implementation      | Registry value   |
| Pydantic AI Model    | Process-local native model implementation used for a request                                 | No               |
| Execution snapshot   | Model fields retained by an accepted Run                                                     | Embedded value   |

An endpoint owner and a wire format are independent facts. An OpenRouter Provider can expose an OpenAI-compatible calling API without becoming an OpenAI Provider. An Ollama Provider can expose the same general format while retaining Ollama-specific discovery and authentication behavior. An OpenAI Provider can allow both Responses and Chat Completions; each Model selects exactly one. Calling-API keys such as `openrouter.chat_completions` are Service registry identifiers retained in public configuration and display, not Pydantic AI enum values.

`ModelProvider` is shortened to Provider within this document when there is no ambiguity. It is not a load-balancing pool, failover policy, model, or generic secret container. `Model` is not an upstream catalog entry: it is a configured scoped alias.

Ownership and automatic visibility follow [Organization-owned configuration](33-identity-and-access-management.md#organization-owned-configuration). An Organization Model references an Organization Provider; a Workspace Model can reference a local or parent Provider. Provider sharing never changes the consuming Run's Workspace or usage attribution.

## Trusted Provider-type and calling-API registry

The distribution assembles a finite registry from trusted code. It combines built-ins with Model integrations registered by deployment-selected `a13n.providers` entry points before readiness. Package installation alone grants no trust; selection names installed metadata rather than an import target. Public requests cannot register code, import a package, invent a calling API, or supply request transformations. Every selected integration still implements this native Model contract and uses the same management and per-request runtime construction path.

Each safe `ModelProviderDefinition` exposes:

```python
class ModelProviderDefinition:
    type: str
    display_name: str
    configuration_schema: JsonObject
    credential_schema: JsonObject
    supported_model_apis: tuple[str, ...]
    default_model_api: str
    model_api_labels: dict[str, str]
    settings_schemas: dict[str, JsonObject]
    supports_model_discovery: bool
```

Each trusted Provider implementation owns one strongly typed configuration model and derives `configuration_schema` from it. The schema defines accepted non-secret fields, bounds, and defaults; `credential_schema` describes write-only credential input separately. Neither contains credential values or operator-private configuration. Provider `type` selects one definition and is immutable after create. Several Providers can select the same type while retaining independent configuration and credentials.

The initial registry follows the native Model implementations supported and tested against the locked Pydantic AI release:

| Provider type          | Allowed calling API keys                                                          | Native Pydantic AI Model binding            |
| ---------------------- | --------------------------------------------------------------------------------- | ------------------------------------------- |
| `openai`               | `openai.responses`, `openai.chat_completions`                                     | `OpenAIResponsesModel`, `OpenAIChatModel`   |
| `anthropic`            | `anthropic.messages`                                                              | `AnthropicModel`                            |
| `google_gemini`        | `google.generate_content`                                                         | `GoogleModel` with `GoogleProvider`         |
| `google_vertex`        | `google.generate_content`                                                         | `GoogleModel` with `GoogleCloudProvider`    |
| `azure_openai`         | `openai.responses`, `openai.chat_completions`                                     | OpenAI Models with `AzureProvider`          |
| `aws_bedrock`          | `bedrock.converse`, `bedrock_mantle.responses`, `bedrock_mantle.chat_completions` | Bedrock Converse and Mantle Models          |
| `openrouter`           | `openrouter.chat_completions`                                                     | `OpenRouterModel`                           |
| `ollama`               | `ollama.chat_completions`                                                         | `OllamaModel`                               |
| `alibaba_model_studio` | `openai.chat_completions`                                                         | `OpenAIChatModel` with `AlibabaProvider`    |
| `deepseek`             | `openai.chat_completions`                                                         | `OpenAIChatModel` with `DeepSeekProvider`   |
| `moonshot`             | `openai.chat_completions`                                                         | `OpenAIChatModel` with `MoonshotAIProvider` |
| `zhipu`                | `openai.chat_completions`                                                         | `OpenAIChatModel` with `ZaiProvider`        |

The table is an executable compatibility registry, not a claim about everything an upstream service documents. For example, this version does not advertise OpenRouter Responses or Anthropic Messages through OpenRouter because the locked Pydantic AI integration does not expose those combinations as supported Model bindings. A new combination requires a trusted registry addition and execution tests.

Provider configuration contains endpoint and authentication mechanics but never an instance-level calling-API choice. The Provider definition's `default_model_api` is one member of `supported_model_apis`. The registry defaults are `openai.responses` for OpenAI and Azure OpenAI, `bedrock.converse` for Bedrock, and the sole allowed API for each other type above. Trusted model-specific information can suggest another allowed API, for example a Mantle binding for an applicable Bedrock model. Defaults are authoring suggestions: every saved Model contains one explicit `model_api`, which runtime never silently changes or replaces with another API.

Every Provider supports a bounded optional `configuration.base_url` and a write-only `extra_headers` map through its native SDK. An override changes connection routing while retaining the Provider's native model profiles, authentication, and calling APIs. Without an override, the adapter uses its official fixed or derived endpoint. Ollama requires a base URL. Azure accepts its typed `resource_endpoint` or a `base_url` override; an explicit legacy `api_version` cannot accompany a v1 endpoint. Vertex retains its project and location. Bedrock uses `base_url` for Converse and a separate optional `mantle_base_url` for Mantle. The Mantle SDK preserves arbitrary gateway path prefixes while selecting its model-specific `/v1` or `/openai/v1` suffix.

The single `openai` type supports official OpenAI and custom OpenAI-compatible endpoints, both Responses and Chat Completions, and `none`, `bearer`, or named API-key-header authentication. Responses is its default. Discovery uses the configured endpoint's OpenAI-style `/models` route; manual creation remains available without that route. There are no separate compatible Provider types.

Extra headers apply to inference and supported Provider operations. Header names are case-insensitive HTTP tokens, normalized to lowercase, and unique. There are at most 32 extra headers; names are limited to 128 characters and values to 2048 printable ASCII characters. Transport-managed headers, Service Thread correlation, and each adapter's authentication or protocol headers cannot be overridden. All header values are encrypted and belong in the separate write-only `extra_headers` map. Provider headers never become Model settings or accepted Run snapshots.

## Model Provider

A Provider has one opaque `ModelProviderId` with the `mprov` kind prefix:

```python
class ModelProvider:
    id: ModelProviderId
    organization_id: OrganizationId
    workspace_id: WorkspaceId | None
    type: str
    name: str
    configuration: JsonObject
    credential_configured: bool
    header_names: tuple[str, ...]
    enabled: bool
    created_by: PrincipalRef
    updated_by: PrincipalRef
    created_at: datetime
    updated_at: datetime
```

`name` is a human-readable, case-insensitively unique name within the owning scope. Provider type is not unique: `OpenAI Production` and `OpenAI Personal` can both have `type="openai"`.

Provider create and update accept a provider-schema-specific write-only `credential` field. Reads return `credential_configured` for the primary credential and `header_names` for saved headers; they never return plaintext, ciphertext, credential shape, masked suffixes, or a reusable Secret identifier. Omitting `credential` on update retains the current value. Supplying null removes it only when the Provider type permits an unauthenticated connection. Supplying another value atomically replaces it.

Model Providers use the same [resource-owned credential protection contract](27-secret-management.md#protection-boundary) as Connectivity resources. Each Provider stores its own ciphertext, nonce, encryption-key identifier, and credential generation. The encrypted value is one bundle containing the primary credential and extra headers. Each accepted update to either secret surface advances that generation exactly once; omitting both leaves it unchanged. Runtime captures the shared encrypted snapshot and decrypts it after closing the database session. Provider-specific credential parsing and optional-authentication rules remain in Model Management. The credential has no independent Secret identity or generic Secret selector.

Provider create and update also accept `extra_headers`: omitted names retain their values, a string sets or replaces one value, and null removes that name. An empty map changes nothing. The whole request, including configuration and header changes, is atomic. A supplied `configuration` replaces ordinary configuration. Only saved header names and the primary-credential presence flag are retained outside the encrypted bundle.

Provider `configuration`, credentials, name, and enabled state are mutable. Provider `type` is immutable. Every update is atomic, audited, and requires the current strong ETag. Provider configuration has no revision number, compatibility snapshot, or historical read API.

Management surfaces select a Provider type, read its safe definition, render ordinary configuration fields, and submit `configuration` plus the separate credential input. The server validates with the implementation-owned model even when a client rendered the schema correctly. Unknown types, unknown configuration fields, and invalid values fail before persistence. A successful save proves configuration validity, not credential or endpoint availability; the explicit test operation performs that external check. Adding a registered type does not require another general-purpose form implementation, although interactive authentication keeps its own domain-specific flow.

Provider setup uses schema defaults for ordinary connection values and asks for credentials and only the additional fields required by the selected integration. After saving, the management client starts discovery when supported and offers selection of candidates for Model creation. Saving the Provider and discovering models are separate operations: discovery failure does not undo the save. Manual Model creation remains available whether discovery succeeds, fails, returns no candidates, or is unsupported.

## Model

A Model has one opaque internal `ModelId` and one memorable external `key`:

```python
class ModelProfile:
    input_modalities: tuple[Literal["text", "image", "audio", "video"], ...] | None
    supports_tools: bool | None
    supports_json_schema_output: bool | None
    supports_json_object_output: bool | None
    supports_image_output: bool | None
    supports_audio_input: bool | None
    supports_thinking: bool | None
    thinking_always_enabled: bool | None


class ModelLimits:
    context_window_tokens: int | None
    max_output_tokens: int | None


class ModelDeclarations:
    thinking_efforts: tuple[ThinkingEffort, ...] = ()
    capabilities: frozenset[ModelCapability] = frozenset()
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    structured_output: bool | None = None
    pricing: ModelPricing | None = None


class Model:
    id: ModelId
    organization_id: OrganizationId
    workspace_id: WorkspaceId | None
    key: str
    provider_id: ModelProviderId
    name: str
    description: str | None
    upstream_model: str
    base_model: str | None
    model_api: str
    settings: JsonObject
    declarations: ModelDeclarations
    enabled: bool
    created_by: PrincipalRef
    updated_by: PrincipalRef
    created_at: datetime
    updated_at: datetime
```

`key` is normalized and case-insensitively unique in every Workspace's visible Model collection: its own Models plus its Organization's Models. Organization Models cannot collide with any Model in a descendant Workspace; Workspace Models cannot collide with a Model in that Workspace or its Organization. Sibling Workspaces can reuse a key. Creation rejects overlap atomically, including concurrent Organization and Workspace creates, with `409 model_key_conflict`. Disabled Models continue reserving their keys. Agent configuration accepts the bare `model_key`, with no scope prefix, precedence, shadowing, or fallback. It is the identifier accepted by public Agent configuration, API, and SDK surfaces. The opaque `id` is retained for internal relationships and observations. `key` and `provider_id` are immutable; selecting a different Provider means creating another Model. Updating the existing Provider's account or endpoint follows the live Provider contract. Several Models may share the same Provider, upstream model, and calling API while retaining distinct keys and settings; that tuple is not unique.

`upstream_model` is an opaque string of 1 through 256 characters passed unchanged to the selected native Pydantic AI Model. It is the invocation identifier required by the configured endpoint, including a model ID, deployment name, or inference endpoint ID. It is not restricted to a bundled or discovered catalog. This lets a Workspace use a newly released upstream model through an existing supported binding before Service's metadata is updated. Clients select `Model.key`; they do not substitute it for the upstream invocation identifier.

`base_model` is a nullable exact name from the installed Pydantic AI model directory. It is a profile and metadata reference, not an outbound identity: runtime always sends `upstream_model`. `model_api` is one key allowed by the selected Provider type. `settings` contains non-secret, JSON-serializable native request defaults and defaults to an empty object. `declarations` is a separate typed object that never enters native request settings. It always serializes with empty `thinking_efforts`, empty `capabilities`, null context/output capacities and structured-output support, and null pricing when values are absent. Effort choices are editable authoring options rather than a runtime allowlist or proof of provider support. Capabilities use the Harness image, video, and audio understanding values; token capacities are positive integers or null. Structured output is coarse advisory support. Optional pricing contains nullable nonnegative USD-per-million-token `input`, `output`, `cache_read`, and `cache_write` rates; zero means free and null means unknown.

Discovery returns read-only `profile` and `limits` supplied by the Provider; unknown profile facts are omitted rather than reported as false, and unknown limits remain null rather than using fabricated numeric defaults. A separate read-only `native_profile` reports trusted facts about the local Pydantic AI calling channel without constructing a client or performing another network request. Catalog and native profiles are independent provenance channels: clients must not treat gateway-wide transport support as a per-model catalog claim. Clients may use catalog modalities and limits to suggest initial declarations, but saved declarations are explicit user-confirmed values and later discovery never overwrites them.

The safe profile deliberately mirrors only serializable Pydantic AI `ModelProfile` facts useful to users. Schema transformers, Python types, system-prompt templates, request transforms, and other execution mechanics remain trusted adapter/runtime code. Profile metadata does not automatically gate Agent save or execution; the upstream response remains authoritative.

The locked Pydantic AI OpenRouter Provider marks its calling channel as accepting unified thinking even for unknown vendor prefixes; Service adds no blanket catalog override and does not derive all effort choices from that gateway fact. When OpenRouter's exhaustive `supported_parameters` list omits reasoning, that per-model catalog fact remains false rather than being replaced by gateway-wide transport support. At dispatch, an explicit unified `thinking` value fails before the remote request when the freshly reconstructed native profile would otherwise drop it as unsupported. `thinking=false` likewise fails for a profile whose thinking is always enabled; positive thinking is accepted when the fresh profile reports either ordinary support or always-enabled thinking. Provider-specific advanced settings remain available under their native behavior. Unknown support is not presented as verified support, and Service does not duplicate provider effort translation.

Model name, description, upstream model, base model, calling API, settings, declarations, and enabled state are mutable under a strong ETag. Changing upstream model or API validates the resulting complete configuration; it never silently drops incompatible settings or reinfers a base model. A supplied null base model clears it. A Model has no version, revision, revision route, historical configuration API, or copy route.

For OpenRouter, the initial API is `openrouter.chat_completions` and an omitted downstream routing setting leaves selection to OpenRouter. Users can configure native routing settings, including `openrouter_provider`, on the Model. Two Models such as `openrouter-claude` and `openrouter-aws-claude` can reference the same upstream Claude ID while one uses platform defaults and the other restricts eligible downstream providers. Downstream identifiers are upstream data rather than a Service release-pinned enum. OpenRouter performs that routing; it does not create additional Service Providers or a routing-policy resource.

## Discovery and manual Model creation

Discovery is a Provider-scoped management operation that returns transient authoring suggestions:

```python
class ModelCandidate:
    upstream_model: str
    display_name: str | None
    suggested_model_api: str
    suggested_settings: JsonObject
    profile: ModelProfile
    native_profile: ModelProfile
    limits: ModelLimits
    parameter_support: dict[str, Literal["supported", "unsupported", "unknown"]]


class ModelDiscovery:
    items: tuple[ModelCandidate, ...]
```

Discovery enumerates candidates using the exact configured Provider's current endpoint and credential, preferring account-filtered information when the upstream exposes it. It excludes models known to be outside this domain; unknown capability metadata does not exclude a new model. Results contain the complete bounded catalog in one `items` array, ordered by upstream ID with duplicates consolidated. There is no client-facing discovery cursor. Clients search and paginate the returned catalog locally and explicitly refresh when they need new data. An enumeration error is distinct from a successful empty list; partial results must not be represented as a complete result.

Static `settings_schemas` live on each safe Provider-type definition, with one entry for every supported calling API. A candidate selects its schema by `suggested_model_api`; changing the API selects another schema from that definition. Individual candidates contain no schema. Missing parameter-support entries mean unknown and need not be serialized.

Discovery returns suggestions, not evidence of successful model invocation. OpenRouter-style catalogs can supply supported parameters, modalities, limits, and defaults. An ID-only catalog such as OpenAI's can enumerate models without supplying those details while still receiving the integration-owned local native profile projection. Trusted bundled metadata may supplement missing facts but is never a membership requirement. Provider-level routing parameters are described independently of model-level sampling or reasoning parameters. A catalog's omission of routing fields does not mark those platform fields unsupported, and aggregate model metadata does not guarantee support on every downstream endpoint.

Results are separate from safe Provider definitions and saved Models. They may be briefly cached within the authorization and configured Provider boundary; connection or credential changes invalidate corresponding cached results. They are not durable resources, do not create or update Models, and never become an execution allowlist.

Callers can select discovered candidates or enter an upstream ID manually, then submit the same ordinary Model create request. `GET /base-models` exposes the exact model names in the installed Pydantic AI directory together with an inferred calling API and its display label when known. Clients prefill an editable name and key, optional base model and API, settings, and declaration suggestions; Provider catalog information remains read-only reference material. Manual creation remains possible without discovery because Provider-type definitions supply every allowed settings schema. Only explicit creation adds a Model in the selected scope, and partial failure when adding several candidates leaves already created Models intact and identifies failed entries.

Creation copies the submitted values into the Model. Subsequent discovery or metadata refresh never changes saved values, removes a saved Model, or disables it. Adopting new suggestions is an explicit edit under the Model's ETag. Changing upstream identity or calling API requires the client to review the saved declarations rather than silently applying candidate facts from the prior selection.

The Service also owns one lifespan-scoped, bounded `httpx2` client and last-good cache for the public models.dev combined catalog. Refresh is single-flight with a whole-operation deadline in addition to HTTP timeouts, an 8 MiB response bound, item limits, one-hour freshness, and a one-minute retry interval after failure. Freshness and retry intervals begin when the refresh attempt completes. Timeout and response cleanup release the single-flight lock; caller cancellation remains cancellation rather than a cache failure. Imports and dispatch never perform catalog I/O. Creation resolves the immutable Provider type in a short read transaction, closes it before cache refresh, and then reauthorizes and writes in a separate short transaction. An unavailable, timed-out, oversized, or malformed catalog yields the last-good declarations, or empty declarations before the first success, and cannot prevent manual creation.

`POST /workspaces/{workspace}/model-catalog/suggestions` and its Organization-scoped counterpart accept a Provider ID and upstream model, with optional `base_model` and `model_api`. They return a match source and zero or more editable candidates containing the base model, inferred or selected API and display label, and declarations. Selection is exclusively against the installed Pydantic AI directory: exact full or model names, separator-normalized equality, then the longest complete model-name token sequence. Matches respect numeric boundaries. Identity resolution precedes API selection: a unique identity remains selected with a null API when the Provider cannot infer a compatible implementation, and an explicit API is applied afterward. Explicitly equivalent OpenAI/OpenAI Chat and Google/Google Cloud namespaces collapse without erasing an exact user-selected base model; a native Provider retains its own equivalent namespace, while a custom endpoint uses the canonical variant. Thus a relay's Claude identity can retain an explicitly selected OpenAI-compatible Chat implementation without acquiring Anthropic model traits. Equal-quality distinct identities are returned as `ambiguous`; explicit null base model returns `none`; and an explicit installed base returns `explicit`.

The models.dev catalog only enriches an already resolved base model; it does not perform a second identity match. Actual-provider entries may supply channel-specific prices, while canonical enrichment is provider-independent and unpriced. On create, one unique resolution is persisted as `base_model`, supplies `model_api` when the request omits it, and supplies declaration fields omitted by the request. An absent or ambiguous resolution requires an explicit API. Explicit null base model suppresses inference. Explicit API always wins, including cross-protocol use. Explicit empty arrays, nulls, false, zero prices, and nested pricing leaves override suggestions. Catalog updates never mutate saved Models, and identity edits never reapply suggestions. `max_output_tokens` remains capacity metadata rather than a request setting. Only exact installed `ThinkingEffort` values from an effort-type catalog option are suggested; toggles, token budgets, `none`, `default`, and `max` do not manufacture choices or require a Service effort translator.

## Parameter schemas and validation

Each Provider definition's `settings_schemas` values are self-contained JSON Schema Draft 2020-12 object schemas over the public native settings keys. They describe accepted JSON types, nested objects, available descriptions, enums, and trusted value constraints for every allowed calling API. The trusted calling-API binding derives them from the applicable Pydantic AI settings type, including provider-specific settings, restricted to the serializable and permitted service surface. Native callables, clients, credential inputs, and code hooks are not configurable settings. Missing or unreadable parameter documentation never prevents schema generation, validation, or execution. Provider/model metadata supplies suggested values, descriptions, and support information, but cannot introduce code, remote schema references, or executable request transformations. Dynamic catalog limits remain descriptive and do not introduce validation assertions that require a remote lookup or change when a metadata cache expires.

`parameter_support` keys are JSON Pointers into the settings object, using native settings names rather than raw upstream request-field names. Trusted integration code maps upstream metadata to these paths. Missing entries mean unknown. Support information is advisory: known unsupported parameters are not recommended by ordinary forms, while unknown parameters remain available in advanced configuration. An absent upstream parameter is not evidence of non-support unless that upstream contract defines the list as exhaustive. Dynamic support metadata does not by itself reject saving or executing a Model. Runtime never requires discovery before inference.

The same schema construction and validation rules serve Model create/update, Agent settings, and Run settings overrides. Type and structural constraints are enforced by the server regardless of frontend checks, with errors pointing to the invalid setting. Known fields use their native shapes; unknown top-level fields are rejected. Each settings object, including `extra_body`, is limited to 64 KiB of UTF-8 JSON and 16 container levels; the final merged settings must satisfy the same bounds. Catalog defaults are suggestions, not values implicitly inserted by schema validation. Suggested settings must satisfy the static schema and reserved-field rules. A metadata outage must not prevent validation through the existing trusted binding. Passing local validation is not a claim that the upstream accepts the settings, and an upstream rejection is surfaced without silently dropping parameters or switching APIs.

`extra_body` is a bounded JSON object for upstream parameters not represented by the installed native settings type, exposed only by bindings that forward it. Bedrock Converse exposes its native `bedrock_additional_model_requests_fields` under the same bounds and reserved-field rules. The locked Google Generate Content binding exposes no arbitrary-body passthrough, so its schema omits `extra_body`. Unknown escape-hatch leaves are not assigned invented schemas or model-support guarantees. Known native settings remain available as named fields. Escape-hatch values are passed through unchanged except for the finite reasoning-conflict paths owned by each calling-API binding. Service does not otherwise validate whether an extension is supported or validate its vendor-specific shape; the caller owns upstream acceptance and native SDK precedence for unprotected parameters.

All settings entry paths enforce the same reserved request boundary. Each calling API defines exact protected body paths for the selected model, Harness-owned messages and instructions, tool declarations and tool choice, streaming mode, and structured-output schema. Provider-owned credential and connection fields are protected at the request root. OpenRouter additionally protects alternate model IDs, presets, and transforms; downstream provider routing for the same model remains configurable. A scalar or array that replaces an ancestor of a protected path is also rejected. Protection follows actual protocol paths, not recursive matching of names in unrelated extension data. For example, Responses protects `text.format` while permitting `text.verbosity`; an unrelated `custom.model` is opaque user data. Non-authentication headers are permitted only within the integration's safe header contract. Native aliases that alter protected controls remain unavailable as settings. These rules cover the supported calling APIs' known control fields; arbitrary future upstream extensions are not assigned invented semantics or a guarantee of safety from undiscovered upstream behavior.

Protected-path validation checks the supplied escape-hatch fields; it does not preserve protected leaves across native SDK merging. OpenAI and Anthropic shallow-merge `extra_body`, so a permitted `text` or `output_config` object replaces the entire generated container, including its structured-output `format`. This also applies to empty objects. Service intentionally allows these cases under caller-owned SDK precedence. Callers use native settings such as `openai_text_verbosity` and `anthropic_effort` when they need those controls while retaining the Harness-generated output format.

Model `settings` are actual request defaults. Discovery `profile` and `limits` are read-only Provider information, never Model create or update fields. A displayed output limit describes upstream capacity; it does not send `max_tokens`. Users set request controls through `settings`. Pydantic AI and trusted integration code retain ownership of the effective native profile and message transformations.

A hosted root and its inline descendants may select different Models and settings. Parent Run acceptance freezes every node's Model execution snapshot and merged settings. The Attempt resolver accepts precisely those Model identities; each outbound request still resolves current Model/Provider eligibility and credentials. An asynchronous child uses its own accepted snapshot when its Worker claims it.

### Settings precedence

Effective settings are resolved at Run acceptance in ascending precedence: saved Model defaults, Agent settings, and explicit Run settings overrides. Ordinary settings merge by top-level key. A later value replaces the entire earlier value at that key, including an object or array; there is no recursive merge. An absent key inherits, and an explicit null is a value only where the setting schema permits it. For example, overriding `openrouter_provider` replaces that whole routing object, while setting `max_tokens` preserves it. An `extra_body` override likewise replaces that whole object.

Reasoning alternatives form one semantic choice instead of independent merge keys. Each calling-API binding owns its finite native keys and relevant escape-hatch paths, including a parent container when even an empty, null, or malformed value suppresses native unified translation. If a higher layer explicitly supplies unified `thinking` or one provider-specific alternative, the resolver removes conflicting inherited alternatives before applying that layer and preserves unrelated settings and unrelated escape-hatch content. Contradictory alternatives in the same layer fail with a safe settings path. Provider-specific settings that the binding defines as one complementary configuration may coexist; for Bedrock Anthropic models, native `thinking` and sibling `output_config` form one such choice. This normalization chooses which supplied configuration wins; Pydantic AI still owns effort translation, snapping, and native request construction.

In a Run override, an absent `settings` field inherits Agent settings, an empty object adds no overrides, and `settings: null` clears Agent settings for that Run so that Model defaults apply. Model PATCH replaces a supplied settings object as a whole; an empty object clears its defaults. To remove an Agent override permanently, save a new AgentRevision without that setting. These map semantics do not change the native meaning of null-valued individual settings.

Switching `model_key` uses the new Model's defaults and declarations and revalidates inherited Agent settings plus explicit Run overrides against that selection. Incompatible values fail rather than being discarded. Replacement attempts and successor operations that preserve an effective configuration reuse its final settings and Harness characteristics without reapplying current defaults, declarations, or suggestions.

## Agent selection and Run snapshot

The [Agent Management contract](28-agent-management.md#agentconfig) owns `AgentModel` and its resolved and effective forms. Agent configuration supplies `model_key`, optional settings overrides, and context-window/context-management policy under its `characteristics` field. Media capabilities are not an Agent or Run override; they come from Model declarations. Agent configuration contains no independently selectable calling API.

Agent Revision creation resolves `model_key` to the internal `model_id`, validates settings against the current Model selection, and retains the stable identity and Agent-authored overrides. It does not copy the Model's mutable API or defaults into the Revision. Invoking any Agent Revision resolves the latest enabled Model at Run acceptance, validates the resulting settings, and freezes the selection. A Model API change therefore affects new Runs, including invocations of older AgentRevisions; incompatible retained Agent settings cause an explicit acceptance failure.

Run acceptance freezes Model identity and the fields that determine outbound request selection:

```python
class ModelExecutionSnapshot:
    schema_version: Literal["1"]
    model_id: ModelId
    model_key: str
    upstream_model: str
    base_model: str | None
    model_api: str
    pricing: ModelPricing | None


class ModelExecutionObservation:
    model_id: ModelId
    model_key: str
    upstream_model: str
    model_api: str
```

The Provider ID is not duplicated in this snapshot because `Model.provider_id` is immutable. Runtime resolves the Provider through the retained `model_id`. The snapshot freezes the accepted editable pricing basis but contains no catalog profile, limits, endpoint, Provider config, credential, or secret. Replacement attempts and explicit Retry reuse the same Model snapshot, so a mid-Run Model or catalog edit does not change upstream model, selected API, or prices. Pricing is configuration for later estimates, not a cost-accounting subsystem; callers must not infer tiered or audio coverage or double-count cached input.

The final merged non-secret settings are stored once in `EffectiveAgentConfig.model.settings`, alongside the execution snapshot and effective Harness model characteristics. One composition path combines Model-declared capabilities and base context window with Agent or Run context policy for primary, reviewer, and nested Agents. Reviewers use the selected Model declarations and default context policy when they expose no policy controls. Every outbound request uses the frozen settings and characteristics. Model edits do not alter them during the Run. Static settings schemas and advisory candidate metadata are not execution snapshots. Retained Runs are reconstructed from their explicit execution selection, settings, and characteristics; compatibility handling must not infer an API from current Model defaults or rewrite historical selections.

Provider values have different semantics. Immediately before every outbound model request, runtime:

1. reads the Model's immutable Provider relationship and current Model/Provider enabled state in a short database session;
2. copies the current Provider configuration and encrypted credential material;
3. closes the database session;
4. authenticates/decrypts and validates the endpoint; and
5. constructs the native Pydantic AI Model for the exact snapshotted calling API.

Provider edits therefore affect the next outbound request, including a later request within the same Run or a replacement attempt. This applies to credential rotation, authentication mode, endpoint, region, project, API version, and `configuration.session_affinity_header`.

`session_affinity_header: str | None` belongs to the Provider connection, not to Model settings or `ModelExecutionSnapshot`. It is an optional normalized HTTP field name; absent/null disables gateway affinity, including on existing Providers. The value uses the shared [UUID v5 request-affinity derivation](../a13n-harness/16-input-model-and-output.md#automatic-request-affinity) of the current Harness `AgentContext.thread_id`, not a user-authored session value or Run ID. No derived value is persisted. Runtime settings validation recognizes the same derived automatic prompt-cache key without admitting raw Thread IDs or other caller cache keys. Service validates caller settings before binding the trusted header on each fresh native Model, outside database sessions. A rename replaces the previously configured header on the next attempt. Static Provider headers, authentication, attribution, and protocol headers cannot collide with the selected name. The strict native settings schema is not widened to accept arbitrary caller headers. Service disables Harness's legacy global header injection. The field is stored in existing Provider configuration JSON, with no new session record or snapshot column.

Console's Provider advanced connection fields offer the shared Harness preset names and custom replacement; Models display the inherited setting with Provider navigation, without a per-Model override. Presets persist concrete names only and do not auto-detect or configure gateways. Sending a header does not guarantee provider pinning. Discovery and ordinary connection probes do not manufacture persistent session IDs or assert successful affinity. An HTTP request already dispatched is not altered or cancelled.

Disabling either the Model or its Provider is a live kill switch: the next outbound request fails closed before dispatch, including a retry in the same Run. Re-enabling permits subsequent requests. Runtime never falls back to another Provider, Model, or calling API.

Service owns outbound inference retries; native SDK automatic retries are disabled. HTTP 429 and 503 responses before a stream is handed to Harness permit up to three total attempts. Each attempt rereads live Model/Provider state and reconstructs the selected native connection outside database sessions. Retry delays use `Retry-After` when available, otherwise bounded exponential backoff; a requested delay over 30 seconds returns the upstream failure instead of retrying early. Non-streamed completion and streamed connection setup share a 600-second budget across attempts and waits unless the effective `timeout` setting supplies another value. After stream handoff, native transport timeouts and enclosing Run or command cancellation/deadlines govern consumption. Request setup must not keep a task-local cancellation scope open across stream handoff because Pydantic AI can consume and close a stream in different tasks. Deadlines are cooperative: Bedrock Converse native calls run in shielded worker threads and cannot be preempted by a shorter Service deadline. Their transport uses a 5-second connect timeout and a 600-second read timeout; cancellation is observed after the current SDK call returns. Authentication errors, other HTTP failures, transport errors, timeouts with uncertain outcomes, and failures after handing a stream to Harness are not automatically replayed. Model tests use the same execution boundary. Native connection construction that can block runs off the event loop, and request-owned clients are closed on success, failure, and cancellation.

## Native construction and endpoint safety

Pydantic AI owns provider invocation, message conversion, streaming, tool calls, structured-output protocol behavior, and the effective native Model profile. Service keeps Provider integration and calling-API selection as two finite trusted registries rather than repeating protocol selection inside every Provider type. For each fresh request and retry, Service asks Pydantic AI for the full profile of the saved exact base-model name and supplies it only when that profile's native API is compatible with the final binding. This retains family and provider additions, including OpenAI-compatible family wire rules, while OpenAI Responses and Chat variants can preserve known GPT traits across an explicit choice between those implementations. A reference from a different native family contributes no profile. The final native binding still constructs the snapshotted `upstream_model` against the current Provider endpoint, authentication, and headers.

One Provider integration owns only:

- Provider config and credential validation;
- optional connection test and model discovery, plus safe candidate metadata mapping;
- endpoint derivation and policy validation; and
- construction of the native Pydantic AI Provider from the current connection state.

The calling-API registry maps each supported API key to its exact native Pydantic AI Model selection and serializable settings contract. Runtime first selects and constructs the current Provider integration, then applies the snapshotted calling-API binding and effective settings to the opaque upstream model name. Compatibility remains the finite `(provider_type, model_api)` allowlist declared by the Provider integration; it is not an allowlist of model IDs. Each binding selects its native Model constructor explicitly, including the distinct Bedrock Mantle APIs.

There is no Service `Interface`, `InterfaceAdapter`, or user-selectable adapter resource.

Every configurable or derived endpoint is validated immediately before dispatch:

- only `http` and `https` are accepted;
- user information, fragments, and credential-bearing or sensitive query parameters are rejected;
- loopback, link-local, cloud-metadata, and non-allowlisted private destinations are denied by default;
- only deployment operators can allow private domains or CIDR ranges;
- DNS answers and redirects are revalidated; and
- official endpoint defaults remain adapter-owned; custom overrides receive the same policy checks.

Provider credentials and configuration are copied under authorization and database consistency, but no transaction or session remains open across decryption, DNS, provider discovery, remote metadata lookup, testing, or model I/O.

## Management API

Provider-type discovery is deployment-scoped and read-only:

```http
GET /api/v1/model-provider-types
```

Workspace Provider resources and commands use:

```http
GET   /api/v1/workspaces/{workspace}/model-providers
POST  /api/v1/workspaces/{workspace}/model-providers
GET   /api/v1/workspaces/{workspace}/model-providers/{provider_id}
PATCH /api/v1/workspaces/{workspace}/model-providers/{provider_id}
POST  /api/v1/workspaces/{workspace}/model-providers/{provider_id}/test
POST  /api/v1/workspaces/{workspace}/model-providers/{provider_id}/discover-models
```

Workspace Model resources and commands use:

```http
GET   /api/v1/workspaces/{workspace}/models
POST  /api/v1/workspaces/{workspace}/models
GET   /api/v1/workspaces/{workspace}/models/{model_id}
PATCH /api/v1/workspaces/{workspace}/models/{model_id}
POST  /api/v1/workspaces/{workspace}/models/{model_id}/test
```

Provider and Model collections use cursor pagination with deterministic `updated_at desc, id desc` ordering. Provider filters include name, type, and enabled state. Model filters include name/key, `provider_id`, enabled state, and Organization-versus-Workspace ownership on Workspace collections.

`discover-models` takes no pagination input and returns a `ModelDiscovery` with one `items` array containing the complete Provider catalog, ordered by `upstream_model` ascending with duplicate IDs consolidated. This transient command result is not an ordinary paginated resource collection. The response has no `next_cursor`, enumeration identity, settings-schema copy, or persisted catalog snapshot. The service follows upstream pagination internally, allowing at most 100 upstream pages, 4 MiB per upstream response, 10,000 unique models, and 32 MiB of serialized discovery output, under the operation deadline. A bound or upstream failure returns a safe operation error rather than truncated results. Filtering non-generative entries does not stop upstream traversal. Unsupported discovery returns `model_discovery_unsupported`.

Every route above also exists under `/api/v1/organizations/{organization}` in place of `/api/v1/workspaces/{workspace}`. Organization routes enumerate, create, and manage Organization-owned resources; Workspace reads include parent resources, while Workspace mutations apply only to locally owned resources.

Create is synchronous and retains no separate idempotency record. Duplicate normalized Provider names return `409 model_provider_name_conflict`; duplicate normalized Model keys return `409 model_key_conflict`. All PATCH routes require `If-Match`; stale state returns `412 precondition_failed` and changes nothing.

Provider testing validates its current connection and authentication without requiring a Model request when a safe provider-native operation exists. List-based probes read one bounded page without enumerating the catalog. An integration without a native probe returns `success=false` and `connection_test_unsupported`, not a connection failure; the user can test a saved Model instead. Model testing uses the Model's saved upstream ID, single API, and default settings; it accepts no separate API selector. A successful catalog list is not a successful Model test. Both tests run outside database transactions, retain no provider response or test resource, may consume quota, and record only a bounded audit event. Discovery follows the same secret and transaction boundaries.

## Lifecycle, authorization, and audit

Service exposes disable/re-enable instead of hard delete for both resources. Provider disable blocks every dependent Model at the next outbound request. A Provider with dependent Models cannot be removed by storage maintenance. Model keys and Provider relationships cannot be reused through a public delete path.

`models.read` permits safe Provider-type, Provider, and Model reads. `models.manage` is required for Provider and Model mutations, credential replacement, connection and Model tests, discovery enumeration, and lifecycle commands. Workspace Viewer receives the read surface, including Organization resources; Workspace Builder and Admin receive both surfaces for local resources and the existing test/discovery use operations for visible Providers and Models. Only Organization Admin through the Organization management boundary mutates Organization resources. Remote metadata results and caches retain the configured Provider's authorization boundary, and every discovery request reauthorizes access. Agent-scoped grants do not confer Workspace Model-management authority.

Create, update, credential replacement/removal, test, discovery, enable, and disable actions produce security audit records. Audit details contain identifiers and safe outcome codes only; they never contain plaintext credentials, encrypted credential material, authorization headers, raw provider errors, prompts, or model output.

Usage and execution observations retain Model identity and selected calling API. Provider identity and type are obtained through the Model's immutable relationship when needed for attribution. The Usage domain owns immutable measure records; Model Management owns current configuration.

## Failure rules

- Unknown Provider type or calling API fails validation.
- A Model API not allowed by its Provider type fails validation.
- Invalid native setting shapes, JSON bounds violations, same-layer reasoning contradictions, and forbidden request overrides fail with safe field-path errors. Unprotected escape-hatch parameters and their interactions with native settings remain the caller's responsibility.
- Missing required Provider credential prevents authenticated discovery, testing, or inference.
- Disabled Model or Provider fails before the next outbound request.
- Missing Model key, missing Provider, or a Model/Provider Workspace mismatch is concealed as not found where required by authorization policy.
- Unsupported native binding, invalid current Provider config, credential decryption failure, or endpoint-policy failure prevents dispatch.
- Discovery failure never invalidates or mutates saved Models.
- Missing catalog entries and unknown parameter support do not prevent manual creation or execution through an allowed binding.
- Upstream parameter rejection is surfaced without silently removing settings.
- Provider changes never cause automatic calling-API fallback.

These rules intentionally avoid a general connection graph, credential resource hierarchy, adapter marketplace, or arbitrary protocol-composition language. New supported combinations extend the finite trusted registry and its tests.
