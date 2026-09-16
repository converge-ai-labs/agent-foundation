# Model Management

## Design Position

Service exposes two Organization- or Workspace-owned resources for primary generative-model execution:

- a `ModelProvider` is one configured account or endpoint; and
- a `Model` is one stable model alias owned by exactly one Provider, with one calling API, editable native settings, and typed Harness declarations.

Provider types and calling APIs are deployment registry values, not resources. A Workspace can create any number of Providers of the same type, such as two OpenAI accounts or two Ollama servers. Credentials, endpoint settings, region, project, and authentication mode belong to the Provider. Upstream model identity, one selected calling API, default request settings, declared input capabilities, context window, and reference prices belong to the Model.

Neither resource has a version or immutable revision. Both are mutable under strong ETag preconditions. An Agent selects only `model_key` and can supply request-setting overrides. Each Run resolves the latest enabled Model when accepted and retains its execution selection and effective settings. Provider configuration is deliberately not frozen: every outbound model request resolves the Model's current Provider configuration and credential.

This contract covers only the primary text or multimodal generative model used by an Agent. Embedding, reranking, moderation, speech, image generation, video generation, and other specialized model resources are outside this domain.

## Boundaries and vocabulary

| Concept              | Meaning                                                                                           | Durable resource |
| -------------------- | ------------------------------------------------------------------------------------------------- | ---------------- |
| Provider type        | Trusted implementation family such as `openai`, `openrouter`, `ollama`, or `aws_bedrock`          | No               |
| Model Provider       | One Organization- or Workspace-owned configured account or endpoint                               | Yes              |
| Calling API          | Request/response contract such as `openai.responses` or `anthropic.messages`                      | Registry key     |
| Model                | Stable scoped alias for one upstream model under one Provider                                     | Yes              |
| Model settings       | Serializable native request defaults for the Model's selected calling API                         | Model value      |
| Model declarations   | User-confirmed Harness facts and reference prices stored separately from native settings          | Model value      |
| Model candidate      | Advisory catalog facts and suggested configuration for an upstream model                          | No               |
| Provider integration | Trusted connection, authentication, endpoint, and connection-probe code selected by Provider type | No               |
| Calling API binding  | Trusted mapping from one calling-API key to one native Pydantic AI Model implementation           | Registry value   |
| Pydantic AI Model    | Process-local native model implementation used for a request                                      | No               |
| Execution snapshot   | Model fields retained by an accepted Run                                                          | Embedded value   |

An endpoint owner and a wire format are independent facts. An OpenRouter Provider can expose an OpenAI-compatible calling API without becoming an OpenAI Provider. An Ollama Provider can expose the same general format while retaining Ollama-specific connection and authentication behavior. An OpenAI Provider can allow both Responses and Chat Completions; each Model selects exactly one. Calling-API keys such as `openrouter.chat_completions` are Service registry identifiers retained in public configuration and display, not Pydantic AI enum values.

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
    catalog_providers: tuple[str, ...]
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
| `minimax`              | `openai.chat_completions`                                                         | `OpenAIChatModel` with `OpenAIProvider`     |

The table is an executable compatibility registry, not a claim about everything an upstream service documents. For example, this version does not advertise OpenRouter Responses or Anthropic Messages through OpenRouter because the locked Pydantic AI integration does not expose those combinations as supported Model bindings. A new combination requires a trusted registry addition and execution tests.

Provider configuration contains endpoint and authentication mechanics but never an instance-level calling-API choice. The Provider definition's `default_model_api` is one member of `supported_model_apis`. The registry defaults are `openai.responses` for OpenAI and Azure OpenAI, `bedrock.converse` for Bedrock, and the sole allowed API for each other type above. Trusted model-specific information can suggest another allowed API, for example a Mantle binding for an applicable Bedrock model. Defaults are authoring suggestions: every saved Model contains one explicit `model_api`, which runtime never silently changes or replaces with another API.

Every Provider supports a bounded optional `configuration.base_url` and a write-only `extra_headers` map through its native SDK. An override changes connection routing while retaining the Provider's native model profiles, authentication, and calling APIs. Without an override, the adapter uses its official fixed or derived endpoint. Ollama requires a base URL. Azure accepts its typed `resource_endpoint` or a `base_url` override; an explicit legacy `api_version` cannot accompany a v1 endpoint. Vertex retains its project and location. Bedrock uses `base_url` for Converse and a separate optional `mantle_base_url` for Mantle. The Mantle SDK preserves arbitrary gateway path prefixes while selecting its model-specific `/v1` or `/openai/v1` suffix.

The single `openai` type supports official OpenAI and custom OpenAI-compatible endpoints, both Responses and Chat Completions, and `none`, `bearer`, or named API-key-header authentication. Responses is its default. A connection probe can use the endpoint's OpenAI-style `/models` route; Model creation never requires that route. There are no separate compatible Provider types.

MiniMax is a trusted named integration using the locked Pydantic AI OpenAI Provider and its Chat Completions binding, because that release has no MiniMax-specific Provider. Its default global endpoint is `https://api.minimax.io/v1`; a China account can override it with `https://api.minimaxi.com/v1`. The standard `minimax` catalog channel supplies model facts. Token Plan catalog channels are excluded from the native selector.

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

Provider setup uses schema defaults and asks for credentials and required connection fields. Saving a Provider never starts model discovery. Model authoring can select a public catalog entry or enter a custom upstream ID.

## Model

A Model has one opaque internal `ModelId` and one memorable external `key`:

```python
class CatalogRef:
    provider: str
    model: str


class ModelDeclarations:
    supports_tools: bool | None = None
    capabilities: frozenset[ModelCapability] = frozenset()
    context_window_tokens: int | None = None
    structured_output: bool | None = None
    pricing: TokenPricing | None = None


class Model:
    id: ModelId
    organization_id: OrganizationId
    workspace_id: WorkspaceId | None
    key: str
    provider_id: ModelProviderId
    name: str
    description: str | None
    upstream_model: str
    catalog_ref: CatalogRef | None
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

`catalog_ref` is an optional exact models.dev provider/model pair, independent of the invocation ID. A gateway alias changes only `upstream_model`. The catalog is not an execution allowlist: a saved reference remains valid after catalog removal, filtering, or outage. A custom model has no reference. `model_api` is always explicit and must be allowed by the Provider type. Saving never performs catalog I/O or guesses identity from aliases.

`settings` contains request defaults. `declarations` contains editable tools support, media capabilities, context window, coarse structured-output support, and pricing. Unknown boolean facts and context window are null; capabilities default to an empty set. Context window is a positive integer. Pydantic AI owns native profile behavior, schema transformation and thinking translation; declarations do not inject executable profile data. The editor exposes `settings` as a JSON object validated against the selected Provider and API, without dedicated `thinking` or `max_tokens` controls. Agent and Run settings can override Model defaults. There is no second thinking-effort enum or output-budget declaration.

Unified `thinking: true` enables thinking when the native profile supports it or always has it enabled; for a profile without thinking support, Service omits this default-on request field before dispatch. Explicit effort levels still fail before dispatch when unsupported. Disabling thinking fails for an always-thinking model.

Model name, description, upstream model, catalog reference, calling API, settings, declarations, and enabled state are mutable under a strong ETag. A null reference clears it. Changing upstream ID or API validates the resulting settings without dropping incompatible values or applying catalog defaults. The Model has no revision, historical configuration API, or copy route.

For OpenRouter, the initial API is `openrouter.chat_completions` and an omitted downstream routing setting leaves selection to OpenRouter. Users can configure native routing settings, including `openrouter_provider`, on the Model. Two Models such as `openrouter-claude` and `openrouter-aws-claude` can reference the same upstream Claude ID while one uses platform defaults and the other restricts eligible downstream providers. Downstream identifiers are upstream data rather than a Service release-pinned enum. OpenRouter performs that routing; it does not create additional Service Providers or a routing-policy resource.

## Catalog and manual Model creation

The authenticated `GET /workspaces/{workspace}/model-catalog` and Organization counterpart expose a public models.dev directory. The collection contains `items`, `status` (`ready`, `stale`, or `unavailable`), and `released_since`. Each item contains its provider-qualified `ref`, name, provider name, release date, editable declarations, and an optional pricing warning. These are authoring values, not durable resources or proof of inference access.

The deployment-wide configurable `models.catalog_released_since` defaults to `2026-04-23`, inclusively. Entries without a valid release date, non-text-generative entries, and unsupported catalog channels are excluded. Supported channels correspond to trusted Provider integrations, plus Volcengine's OpenAI-compatible channel. Directory updates can make new model IDs available without a Service release; new native protocols and adaptations still require trusted code.

Provider definitions expose their native catalog namespaces. Console initially shows only those channels, including when the Provider has a custom endpoint. Catalog entries expose a provider-independent `identity` and canonical display name when their IDs can be unambiguously associated with the independent model directory. Known channel namespace and region wrappers do not create additional model identities; dates and editions remain significant. Unknown or ambiguous associations retain a channel-qualified identity rather than merging by display name.

Console groups entries by identity. One native offering fills its exact upstream ID and declarations; multiple native offerings require an explicit Provider model variant selection before applying any upstream ID or price. Only the OpenAI Provider offers an explicit "Other models (compatible)" entry to browse other identities. Cross-channel selection supplies reference metadata, leaves the unknown gateway upstream ID empty, and initially selects OpenAI Chat Completions. It does not assert that an official OpenAI endpoint serves that model. Users supply a compatible connection and its invocation ID; the API remains editable. The selected channel's price is used when available; otherwise the editor uses the same identity's official channel price when present. These are editable reference prices, including on custom endpoints and cross-channel connections, and may differ from gateway billing. Custom upstream entry remains available for every Provider.

Selecting a directory entry immediately applies its reference, upstream ID, and declarations to the draft. For a non-native model selected through a compatible OpenAI connection, the unknown upstream ID remains blank for the user to enter. Refresh does not modify drafts, saved Models, or accepted Runs. Create persists exactly submitted values without enrichment or identity guessing.

A lifespan-owned bounded client maintains one public last-good snapshot, with single-flight refresh, a 30-second whole-operation deadline, an 8 MiB response limit, at most 1,000 providers and 50,000 admitted-channel model entries, one-hour freshness, and one-minute retry after failure. Timers begin at completion. Malformed individual entries are excluded; malformed envelopes and refresh failures retain last-good data with `stale` status, or an empty `unavailable` collection before first success. Cancellation propagates and releases the refresh lock. Authorization completes before network I/O; imports, Model writes, and inference never fetch the directory.

### Token pricing

Pricing stores complete USD-per-million-token cliff tiers, not incremental brackets:

```json
{
  "currency": "USD",
  "unit": "million_tokens",
  "tier_basis": "input_tokens",
  "tiers": [
    {"above": null, "rates": {"input": "5", "output": "30", "cache_read": "0.5", "cache_write": null}},
    {"above": 200000, "rates": {"input": "10", "output": "45", "cache_read": "1", "cache_write": null}}
  ]
}
```

The first tier is the base (`above=null`); subsequent nonnegative integer thresholds are strictly ascending and unique. There are one through 32 tiers. Total input tokens, including cached input, select the last threshold strictly exceeded. Every token dimension uses that tier for the whole request. Exact decimal strings serialize finite nonnegative rates; zero is free and null is unknown. Saved tiers contain complete rates and never inherit from earlier tiers. Catalog import materializes models.dev base-rate overrides. Unsupported tier conditions or invalid rates produce a warning and no imported table rather than silently flattening prices.

Accepted Runs freeze these tables per Model. The inherited Harness cost policy selects by the request's original Model ID, never an upstream response alias, and reuses native token accounting. Missing prices for a used dimension, unknown model selection, inconsistent cache counts, or audio usage decline valuation under the Harness observation contract. The table is a reference estimate, not an invoice or currency/settlement system.

## Parameter schemas and validation

Each Provider definition's `settings_schemas` values are self-contained JSON Schema Draft 2020-12 object schemas over the public native settings keys. They describe accepted JSON types, nested objects, available descriptions, enums, and trusted value constraints for every allowed calling API. The trusted calling-API binding derives them from the applicable Pydantic AI settings type, including provider-specific settings, restricted to the serializable and permitted service surface. Native callables, clients, credential inputs, and code hooks are not configurable settings. Missing or unreadable parameter documentation never prevents schema generation, validation, or execution. Provider/model metadata supplies suggested values, descriptions, and support information, but cannot introduce code, remote schema references, or executable request transformations. Dynamic catalog limits remain descriptive and do not introduce validation assertions that require a remote lookup or change when a metadata cache expires.

The same schema construction and validation rules serve Model create/update, Agent settings, and Run settings overrides. Type and structural constraints are enforced by the server regardless of frontend checks, with errors pointing to the invalid setting. Known fields use their native shapes; unknown top-level fields are rejected. Each settings object, including `extra_body`, is limited to 64 KiB of UTF-8 JSON and 16 container levels; the final merged settings must satisfy the same bounds. Catalog defaults are suggestions, not values implicitly inserted by schema validation. Suggested settings must satisfy the static schema and reserved-field rules. A metadata outage must not prevent validation through the existing trusted binding. Passing local validation is not a claim that the upstream accepts the settings, and an upstream rejection is surfaced without silently dropping parameters or switching APIs.

`extra_body` is a bounded JSON object for upstream parameters not represented by the installed native settings type, exposed only by bindings that forward it. Bedrock Converse exposes its native `bedrock_additional_model_requests_fields` under the same bounds and reserved-field rules. The locked Google Generate Content binding exposes no arbitrary-body passthrough, so its schema omits `extra_body`. Unknown escape-hatch leaves are not assigned invented schemas or model-support guarantees. Known native settings remain available as named fields. Escape-hatch values are passed through unchanged except for the finite reasoning-conflict paths owned by each calling-API binding. Service does not otherwise validate whether an extension is supported or validate its vendor-specific shape; the caller owns upstream acceptance and native SDK precedence for unprotected parameters.

All settings entry paths enforce the same reserved request boundary. Each calling API defines exact protected body paths for the selected model, Harness-owned messages and instructions, tool declarations and tool choice, streaming mode, and structured-output schema. Provider-owned credential and connection fields are protected at the request root. OpenRouter additionally protects alternate model IDs, presets, and transforms; downstream provider routing for the same model remains configurable. A scalar or array that replaces an ancestor of a protected path is also rejected. Protection follows actual protocol paths, not recursive matching of names in unrelated extension data. For example, Responses protects `text.format` while permitting `text.verbosity`; an unrelated `custom.model` is opaque user data. Non-authentication headers are permitted only within the integration's safe header contract. Native aliases that alter protected controls remain unavailable as settings. These rules cover the supported calling APIs' known control fields; arbitrary future upstream extensions are not assigned invented semantics or a guarantee of safety from undiscovered upstream behavior.

Protected-path validation checks the supplied escape-hatch fields; it does not preserve protected leaves across native SDK merging. OpenAI and Anthropic shallow-merge `extra_body`, so a permitted `text` or `output_config` object replaces the entire generated container, including its structured-output `format`. This also applies to empty objects. Service intentionally allows these cases under caller-owned SDK precedence. Callers use native settings such as `openai_text_verbosity` and `anthropic_effort` when they need those controls while retaining the Harness-generated output format.

Model `settings` are actual request defaults. Pydantic AI and trusted integration code own the native profile and message transformations.

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
    catalog_ref: CatalogRef | None
    model_api: str
    pricing: TokenPricing | None


class ModelExecutionObservation:
    model_id: ModelId
    model_key: str
    upstream_model: str
    model_api: str
```

The Provider ID is not duplicated in this snapshot because `Model.provider_id` is immutable. Runtime resolves the Provider through the retained `model_id`. The snapshot freezes the accepted editable pricing basis but contains no catalog profile, limits, endpoint, Provider config, credential, or secret. Replacement attempts and explicit Retry reuse the same Model snapshot, so a mid-Run Model or catalog edit does not change upstream model, selected API, or prices. Pricing supplies reference estimates under the token-pricing contract above, not settlement; audio prices are not inferred.

The final merged non-secret settings are stored once in `EffectiveAgentConfig.model.settings`, alongside the execution snapshot and effective Harness model characteristics. One composition path combines Model-declared capabilities and base context window with Agent or Run context policy for primary, reviewer, and nested Agents. Reviewers use the selected Model declarations and default context policy when they expose no policy controls. Every outbound request uses the frozen settings and characteristics. Model edits do not alter them during the Run. Static settings schemas and advisory candidate metadata are not execution snapshots. Retained Runs are reconstructed from their explicit execution selection, settings, and characteristics; compatibility handling must not infer an API from current Model defaults or rewrite historical selections.

Provider values have different semantics. Immediately before every outbound model request, runtime:

1. reads the Model's immutable Provider relationship and current Model/Provider enabled state in a short database session;
2. copies the current Provider configuration and encrypted credential material;
3. closes the database session;
4. authenticates/decrypts and validates the endpoint; and
5. constructs the native Pydantic AI Model for the exact snapshotted calling API.

Provider edits therefore affect the next outbound request, including a later request within the same Run or a replacement attempt. This applies to credential rotation, authentication mode, endpoint, region, project, API version, and `configuration.session_affinity_header`.

`session_affinity_header: str | None` belongs to the Provider connection, not to Model settings or `ModelExecutionSnapshot`. It is an optional normalized HTTP field name; absent/null disables gateway affinity, including on existing Providers. The value uses the shared [UUID v5 request-affinity derivation](../a13n-harness/16-input-model-and-output.md#automatic-request-affinity) of the current Harness `AgentContext.thread_id`, not a user-authored session value or Run ID. No derived value is persisted. Runtime settings validation recognizes the same derived automatic prompt-cache key without admitting raw Thread IDs or other caller cache keys. Service validates caller settings before binding the trusted header on each fresh native Model, outside database sessions. A rename replaces the previously configured header on the next attempt. Static Provider headers, authentication, attribution, and protocol headers cannot collide with the selected name. The strict native settings schema is not widened to accept arbitrary caller headers. Service disables Harness's legacy global header injection. The field is stored in existing Provider configuration JSON, with no new session record or snapshot column.

Console's Provider advanced connection fields offer the shared Harness preset names and custom replacement; Models display the inherited setting with Provider navigation, without a per-Model override. Presets persist concrete names only and do not auto-detect or configure gateways. Sending a header does not guarantee provider pinning. Ordinary connection probes do not manufacture persistent session IDs or assert successful affinity. An HTTP request already dispatched is not altered or cancelled.

Disabling either the Model or its Provider is a live kill switch: the next outbound request fails closed before dispatch, including a retry in the same Run. Re-enabling permits subsequent requests. Runtime never falls back to another Provider, Model, or calling API.

Service owns outbound inference retries; native SDK automatic retries are disabled. HTTP 429 and 503 responses before a stream is handed to Harness permit up to three total attempts. Each attempt rereads live Model/Provider state and reconstructs the selected native connection outside database sessions. Retry delays use `Retry-After` when available, otherwise bounded exponential backoff; a requested delay over 30 seconds returns the upstream failure instead of retrying early. Non-streamed completion and streamed connection setup share a 600-second budget across attempts and waits unless the effective `timeout` setting supplies another value. After stream handoff, native transport timeouts and enclosing Run or command cancellation/deadlines govern consumption. Request setup must not keep a task-local cancellation scope open across stream handoff because Pydantic AI can consume and close a stream in different tasks. Deadlines are cooperative: Bedrock Converse native calls run in shielded worker threads and cannot be preempted by a shorter Service deadline. Their transport uses a 5-second connect timeout and a 600-second read timeout; cancellation is observed after the current SDK call returns. Authentication errors, other HTTP failures, transport errors, timeouts with uncertain outcomes, and failures after handing a stream to Harness are not automatically replayed. Model tests use the same execution boundary. Native connection construction that can block runs off the event loop, and request-owned clients are closed on success, failure, and cancellation.

## Native construction and endpoint safety

Pydantic AI owns provider invocation, message conversion, streaming, tool calls, structured-output protocol behavior, and the effective native Model profile. Service keeps Provider integration and calling-API selection as two finite trusted registries rather than repeating protocol selection inside every Provider type. For each fresh request and retry, Service uses the catalog model ID with the actual native Provider's profile resolver when its catalog namespace matches. A generic OpenAI Chat connection can also use explicitly supported OpenAI-compatible family profile resolvers. Cross-protocol references never transplant a profile; absent references use the native Provider's upstream-name behavior. This retains family and provider additions, including OpenAI-compatible family wire rules, while OpenAI Responses and Chat variants can preserve known GPT traits across an explicit choice between those implementations. A reference from a different native family contributes no profile. The final native binding still constructs the snapshotted `upstream_model` against the current Provider endpoint, authentication, and headers.

One Provider integration owns only:

- Provider config and credential validation;
- an optional bounded connection probe;
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

Provider credentials and configuration are copied under authorization and database consistency, but no transaction or session remains open across decryption, DNS, remote metadata lookup, testing, or model I/O.

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
```

Workspace Model resources and commands use:

```http
GET   /api/v1/workspaces/{workspace}/model-catalog
GET   /api/v1/workspaces/{workspace}/models
POST  /api/v1/workspaces/{workspace}/models
GET   /api/v1/workspaces/{workspace}/models/{model_id}
PATCH /api/v1/workspaces/{workspace}/models/{model_id}
POST  /api/v1/workspaces/{workspace}/models/{model_id}/test
```

Provider and Model collections use cursor pagination with deterministic `updated_at desc, id desc` ordering. Provider filters include name, type, and enabled state. Model filters include name/key, `provider_id`, enabled state, and Organization-versus-Workspace ownership on Workspace collections.

Every route above also exists under `/api/v1/organizations/{organization}` in place of `/api/v1/workspaces/{workspace}`. Organization routes enumerate, create, and manage Organization-owned resources; Workspace reads include parent resources, while Workspace mutations apply only to locally owned resources.

Create is synchronous and retains no separate idempotency record. Duplicate normalized Provider names return `409 model_provider_name_conflict`; duplicate normalized Model keys return `409 model_key_conflict`. All PATCH routes require `If-Match`; stale state returns `412 precondition_failed` and changes nothing.

Provider testing validates its current connection and authentication without requiring a Model request when a safe provider-native operation exists. List-based probes read one bounded page without enumerating the catalog. An integration without a native probe returns `success=false` and `connection_test_unsupported`, not a connection failure; the user can test a saved Model instead. Model testing uses the Model's saved upstream ID, single API, and default settings; it accepts no separate API selector. A successful catalog list is not a successful Model test. Both tests run outside database transactions, retain no provider response or test resource, may consume quota, and record only a bounded audit event.

## Lifecycle, authorization, and audit

Service exposes disable/re-enable instead of hard delete for both resources. Provider disable blocks every dependent Model at the next outbound request. A Provider with dependent Models cannot be removed by storage maintenance. Model keys and Provider relationships cannot be reused through a public delete path.

`models.read` permits safe Provider-type, Provider, and Model reads. `models.manage` is required for Provider and Model mutations, credential replacement, connection and Model tests, and lifecycle commands. Workspace Viewer receives the read surface, including Organization resources; Workspace Builder and Admin receive both surfaces for local resources and the existing test use operations for visible Providers and Models. Only Organization Admin through the Organization management boundary mutates Organization resources. Catalog reads require Models read authority and contain only public metadata; no configured Provider credential is sent to models.dev. Agent-scoped grants do not confer Workspace Model-management authority.

Create, update, credential replacement/removal, test, enable, and disable actions produce security audit records. Audit details contain identifiers and safe outcome codes only; they never contain plaintext credentials, encrypted credential material, authorization headers, raw provider errors, prompts, or model output.

Usage and execution observations retain Model identity and selected calling API. Provider identity and type are obtained through the Model's immutable relationship when needed for attribution. The Usage domain owns immutable measure records; Model Management owns current configuration.

## Failure rules

- Unknown Provider type or calling API fails validation.
- A Model API not allowed by its Provider type fails validation.
- Invalid native setting shapes, JSON bounds violations, same-layer reasoning contradictions, and forbidden request overrides fail with safe field-path errors. Unprotected escape-hatch parameters and their interactions with native settings remain the caller's responsibility.
- Missing required Provider credential prevents authenticated testing or inference.
- Disabled Model or Provider fails before the next outbound request.
- Missing Model key, missing Provider, or a Model/Provider Workspace mismatch is concealed as not found where required by authorization policy.
- Unsupported native binding, invalid current Provider config, credential decryption failure, or endpoint-policy failure prevents dispatch.
- Catalog refresh failure never invalidates or mutates saved Models.
- Missing catalog entries and unknown parameter support do not prevent manual creation or execution through an allowed binding.
- Upstream parameter rejection is surfaced without silently removing settings.
- Provider changes never cause automatic calling-API fallback.

These rules intentionally avoid a general connection graph, credential resource hierarchy, adapter marketplace, or arbitrary protocol-composition language. New supported combinations extend the finite trusted registry and its tests.
