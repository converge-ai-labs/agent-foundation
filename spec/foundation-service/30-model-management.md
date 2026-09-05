# Model Management

## Design Position

Foundation exposes two Workspace resources for primary generative-model execution:

- a `ModelProvider` is one configured account or endpoint; and
- a `Model` is one stable Workspace model alias owned by exactly one Provider, with one calling API and editable default settings.

Provider types and calling APIs are deployment registry values, not resources. A Workspace can create any number of Providers of the same type, such as two OpenAI accounts or two Ollama servers. Credentials, endpoint settings, region, project, and authentication mode belong to the Provider. Upstream model identity, one selected calling API, and default request settings belong to the Model.

Neither resource has a version or immutable revision. Both are mutable under strong ETag preconditions. An Agent selects only `model_key` and can supply request-setting overrides. Each Run resolves the latest enabled Model when accepted and retains its execution selection and effective settings. Provider configuration is deliberately not frozen: every outbound model request resolves the Model's current Provider configuration and credential.

This contract covers only the primary text or multimodal generative model used by an Agent. Embedding, reranking, moderation, speech, image generation, video generation, and other specialized model resources are outside this domain.

## Boundaries and vocabulary

| Concept              | Meaning                                                                                    | Durable resource |
| -------------------- | ------------------------------------------------------------------------------------------ | ---------------- |
| Provider type        | Trusted implementation family such as `openai`, `openrouter`, `ollama`, or `aws_bedrock`   | No               |
| Model Provider       | One Workspace-owned configured account or endpoint                                         | Yes              |
| Calling API          | Request/response contract such as `openai.responses` or `anthropic.messages`               | Registry key     |
| Model                | Stable Workspace alias for one upstream model under one Provider                           | Yes              |
| Model settings       | Serializable native request defaults for the Model's selected calling API                  | Model value      |
| Model description    | Suggested configuration and parameter schema for an upstream model under a Provider        | No               |
| Provider integration | Trusted connection, authentication, endpoint, and discovery code selected by Provider type | No               |
| Calling API binding  | Trusted mapping from one calling-API key to one native Pydantic AI Model implementation    | Registry value   |
| Pydantic AI Model    | Process-local native model implementation used for a request                               | No               |
| Execution snapshot   | Model fields retained by an accepted Run                                                   | Embedded value   |

An endpoint owner and a wire format are independent facts. An OpenRouter Provider can expose an OpenAI-compatible calling API without becoming an OpenAI Provider. An Ollama Provider can expose the same general format while retaining Ollama-specific discovery and authentication behavior. An OpenAI Provider can allow both Responses and Chat Completions; each Model selects exactly one. Calling-API keys such as `openrouter.chat_completions` are Foundation registry identifiers retained in public configuration and display, not Pydantic AI enum values.

`ModelProvider` is shortened to Provider within this document when there is no ambiguity. It is not a load-balancing pool, failover policy, model, or generic secret container. `Model` is not an upstream catalog entry: it is a configured Workspace alias.

## Trusted Provider-type and calling-API registry

The distribution assembles a finite registry from trusted code. Public requests cannot register code, import a package, invent a calling API, or supply request transformations. Package installation alone grants no trust.

Each safe `ModelProviderDefinition` exposes:

```python
class ModelProviderDefinition:
    type: str
    display_name: str
    configuration_schema: JsonObject
    credential_schema: JsonObject
    supported_model_apis: tuple[str, ...]
    default_model_api: str
    supports_model_discovery: bool
```

Each trusted Provider implementation owns one strongly typed configuration model and derives `configuration_schema` from it. The schema defines accepted non-secret fields, bounds, and defaults; `credential_schema` describes write-only credential input separately. Neither contains credential values or operator-private configuration. Provider `type` selects one definition and is immutable after create. Several Providers can select the same type while retaining independent configuration and credentials.

The initial registry follows the native Model implementations supported and tested against the locked Pydantic AI release:

| Provider type          | Allowed calling API keys                                                          | Native Pydantic AI Model binding                                |
| ---------------------- | --------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| `openai`               | `openai.responses`, `openai.chat_completions`                                     | `OpenAIResponsesModel`, `OpenAIChatModel`                       |
| `anthropic`            | `anthropic.messages`                                                              | `AnthropicModel`                                                |
| `google_gemini`        | `google.generate_content`                                                         | `GoogleModel` with `GoogleProvider`                             |
| `google_vertex`        | `google.generate_content`                                                         | `GoogleModel` with `GoogleCloudProvider`                        |
| `azure_openai`         | `openai.responses`, `openai.chat_completions`                                     | OpenAI Models with `AzureProvider`                              |
| `aws_bedrock`          | `bedrock.converse`, `bedrock_mantle.responses`, `bedrock_mantle.chat_completions` | Bedrock Converse and Mantle Models                              |
| `openrouter`           | `openrouter.chat_completions`                                                     | `OpenRouterModel`                                               |
| `ollama`               | `ollama.chat_completions`                                                         | `OllamaModel`                                                   |
| `alibaba_model_studio` | `openai.chat_completions`                                                         | `OpenAIChatModel` with `AlibabaProvider`                        |
| `deepseek`             | `openai.chat_completions`                                                         | `OpenAIChatModel` with `DeepSeekProvider`                       |
| `moonshot`             | `openai.chat_completions`                                                         | `OpenAIChatModel` with `MoonshotAIProvider`                     |
| `zhipu`                | `openai.chat_completions`                                                         | `OpenAIChatModel` with `ZaiProvider`                            |
| `openai_compatible`    | `openai.responses`, `openai.chat_completions`                                     | OpenAI Models with a bounded generic OpenAI-compatible provider |

The table is an executable compatibility registry, not a claim about everything an upstream service documents. For example, this version does not advertise OpenRouter Responses or Anthropic Messages through OpenRouter because the locked Pydantic AI integration does not expose those combinations as supported Model bindings. A new combination requires a trusted registry addition and execution tests.

Provider configuration contains endpoint and authentication mechanics but never an instance-level calling-API choice. The Provider definition's `default_model_api` is one member of `supported_model_apis`. The registry defaults are `openai.responses` for OpenAI and Azure OpenAI, `bedrock.converse` for Bedrock, `openai.chat_completions` for the generic OpenAI-compatible type, and the sole allowed API for each other type above. Trusted model-specific information can suggest another allowed API, for example a Mantle binding for an applicable Bedrock model. Defaults are authoring suggestions: every saved Model contains one explicit `model_api`, which runtime never silently changes or replaces with another API.

Official direct Provider types use fixed or typed derived endpoints. The generic `openai_compatible` type accepts a bounded `base_url` and an explicit `none`, `bearer`, or named API-key-header authentication mode. Provider-specific values such as Azure resource endpoint/API version, Vertex project and location, Bedrock region, or Ollama base URL remain Provider configuration.

## Model Provider

A Provider has one opaque `ModelProviderId` with the `mprov` kind prefix:

```python
class ModelProvider:
    id: ModelProviderId
    workspace_id: WorkspaceId
    type: str
    name: str
    configuration: JsonObject
    credential_configured: bool
    enabled: bool
    created_by: PrincipalRef
    updated_by: PrincipalRef
    created_at: datetime
    updated_at: datetime
```

`name` is a human-readable, case-insensitively unique name within the Workspace. Provider type is not unique: `OpenAI Production` and `OpenAI Personal` can both have `type="openai"`.

Provider create and update accept a provider-schema-specific write-only `credential` field. Reads return only `credential_configured`; they never return plaintext, ciphertext, credential shape, masked suffixes, or a reusable Secret identifier. Omitting `credential` on update retains the current value. Supplying null removes it only when the Provider type permits an unauthenticated connection. Supplying another value atomically replaces it.

Model Providers use the same [resource-owned credential protection contract](27-secret-management.md#protection-boundary) as Connectivity resources. Each Provider stores its own ciphertext, nonce, encryption-key identifier, and credential generation. Every accepted credential replacement or explicit removal advances that generation exactly once; omitting the credential on update leaves it unchanged. Runtime captures the shared encrypted snapshot and decrypts it after closing the database session. Provider-specific credential parsing and optional-authentication rules remain in Model Management. The credential has no independent Secret identity or generic Secret selector.

Provider `configuration`, credential, name, and enabled state are mutable. Provider `type` is immutable. Every update is atomic, audited, and requires the current strong ETag. Provider configuration has no revision number, compatibility snapshot, or historical read API.

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


class Model:
    id: ModelId
    workspace_id: WorkspaceId
    key: str
    provider_id: ModelProviderId
    name: str
    description: str | None
    upstream_model: str
    model_api: str
    settings: JsonObject
    enabled: bool
    created_by: PrincipalRef
    updated_by: PrincipalRef
    created_at: datetime
    updated_at: datetime
```

`key` is normalized and case-insensitively unique within the Workspace. It is the identifier accepted by public Agent configuration, API, and SDK surfaces. The opaque `id` is retained for internal relationships and observations. `key` and `provider_id` are immutable; selecting a different Provider means creating another Model. Updating the existing Provider's account or endpoint follows the live Provider contract. Several Models may share the same Provider, upstream model, and calling API while retaining distinct keys and settings; that tuple is not unique.

`upstream_model` is an opaque string of 1 through 256 characters passed unchanged to the selected native Pydantic AI Model. It is the invocation identifier required by the configured endpoint, including a model ID, deployment name, or inference endpoint ID. It is not restricted to a bundled or discovered catalog. This lets a Workspace use a newly released upstream model through an existing supported binding before Foundation's metadata is updated. Clients select `Model.key`; they do not substitute it for the upstream invocation identifier.

`model_api` is one key allowed by the selected Provider type. `settings` contains non-secret, JSON-serializable native request defaults and defaults to an empty object. Model resources contain no editable or persisted capability metadata. Discovery and description return read-only `profile` and `limits` supplied by the Provider; unknown facts remain null rather than false or fabricated numeric defaults.

The safe profile deliberately mirrors only serializable Pydantic AI `ModelProfile` facts useful to users. Schema transformers, Python types, system-prompt templates, request transforms, and other execution mechanics remain trusted adapter/runtime code. Profile metadata does not automatically gate Agent save or execution; the upstream response remains authoritative.

Model name, description, upstream model, calling API, settings, and enabled state are mutable under a strong ETag. Changing upstream model or API validates the resulting complete configuration; it never silently drops incompatible settings. A Model has no version, revision, revision route, historical configuration API, or copy route.

For OpenRouter, the initial API is `openrouter.chat_completions` and an omitted downstream routing setting leaves selection to OpenRouter. Users can configure native routing settings, including `openrouter_provider`, on the Model. Two Models such as `openrouter-claude` and `openrouter-aws-claude` can reference the same upstream Claude ID while one uses platform defaults and the other restricts eligible downstream providers. Downstream identifiers are upstream data rather than a Foundation release-pinned enum. OpenRouter performs that routing; it does not create additional Foundation Providers or a routing-policy resource.

## Discovery and manual Model creation

Discovery and description are Provider-scoped management operations. They share one transient suggested-configuration value:

```python
class ModelDescription:
    upstream_model: str
    display_name: str | None
    suggested_model_api: str
    suggested_settings: JsonObject
    profile: ModelProfile
    limits: ModelLimits
    settings_schema: JsonObject
    parameter_support: dict[str, Literal["supported", "unsupported", "unknown"]]
```

Discovery enumerates candidates using the exact configured Provider's current endpoint and credential, preferring account-filtered information when the upstream exposes it. It excludes models known to be outside this domain; unknown capability metadata does not exclude a new model. Results contain the complete bounded catalog in one `items` array, ordered by upstream ID with duplicates consolidated. There is no client-facing discovery cursor. Clients search and paginate the returned catalog locally and explicitly refresh when they need new data. An enumeration error is distinct from a successful empty list; partial results must not be represented as a complete result.

Description accepts one arbitrary bounded upstream identifier and an optional allowed API. An explicitly supplied API is retained; otherwise trusted model-specific information or the Provider definition supplies the suggested API. It works without prior discovery or a saved Model. When remote metadata is absent or unavailable, it still returns the trusted settings schema, available defaults, and unknown support/profile/limit fields. Structural validation, authorization, and unsupported API errors do not become metadata fallbacks. New model IDs do not require an entry in a bundled metadata table.

Both operations return suggestions, not evidence of successful model invocation. OpenRouter-style catalogs can supply supported parameters, modalities, limits, and defaults. An ID-only catalog such as OpenAI's can enumerate models without supplying those details. Trusted bundled metadata may supplement missing facts but is never a membership requirement. Provider-level routing parameters are described independently of model-level sampling or reasoning parameters. A catalog's omission of routing fields does not mark those platform fields unsupported, and aggregate model metadata does not guarantee support on every downstream endpoint.

Results are separate from safe Provider definitions and saved Workspace Models. They may be briefly cached within the authorization and configured Provider boundary; connection or credential changes invalidate corresponding cached results. They are not durable resources, do not create or update Models, and never become an execution allowlist.

Callers select discovered descriptions or request a description for a manually entered upstream ID, then submit the same ordinary Model create request. Clients prefill an editable name and key, one API, and settings; Provider capability information remains read-only reference material. The create request always contains the chosen key and explicit API; optional settings default to an empty object. Manual creation remains possible without calling description or discovery. Only explicit creation adds a Workspace Model, and partial failure when adding several candidates leaves already created Models intact and identifies failed entries.

Creation copies the submitted values into the Model. Subsequent discovery, description, or metadata refresh never changes saved values, removes a saved Model, or disables it. Adopting new suggestions is an explicit edit under the Model's ETag. Editing an existing Model requests a description using its current Provider, upstream ID, and API but keeps the saved values as the editor's values.

## Parameter schemas and validation

`settings_schema` is a self-contained JSON Schema Draft 2020-12 object schema over the public native settings keys. It describes accepted JSON types, nested objects, available descriptions, enums, and trusted value constraints for the selected Provider, API, and upstream model. The trusted calling-API binding derives it from the applicable Pydantic AI settings type, including provider-specific settings, restricted to the serializable and permitted service surface. Native callables, clients, credential inputs, and code hooks are not configurable settings. Missing or unreadable parameter documentation never prevents schema generation, validation, or execution. Provider/model metadata supplies suggested values, descriptions, and support information, but cannot introduce code, remote schema references, or executable request transformations. Dynamic catalog limits remain descriptive and do not introduce validation assertions that require a remote lookup or change when a metadata cache expires.

`parameter_support` keys are JSON Pointers into the settings object, using native settings names rather than raw upstream request-field names. Trusted integration code maps upstream metadata to these paths. Missing entries mean unknown. Support information is advisory: known unsupported parameters are not recommended by ordinary forms, while unknown parameters remain available in advanced configuration. An absent upstream parameter is not evidence of non-support unless that upstream contract defines the list as exhaustive. Dynamic support metadata does not by itself reject saving or executing a Model. Runtime never requires a discovery or description request before inference.

The same schema construction and validation rules serve Model create/update, Agent settings, Run settings overrides, and descriptions. Type and structural constraints are enforced by the server regardless of frontend checks, with errors pointing to the invalid setting. Known fields use their native shapes; unknown top-level fields are rejected. Each settings object, including `extra_body`, is limited to 64 KiB of UTF-8 JSON and 16 container levels; the final merged settings must satisfy the same bounds. Defaults in a description are suggestions, not values implicitly inserted by schema validation. Suggested settings must satisfy the returned schema and reserved-field rules. A metadata outage must not prevent validation through the existing trusted binding. Passing local validation is not a claim that the upstream accepts the settings, and an upstream rejection is surfaced without silently dropping parameters or switching APIs.

`extra_body` is a bounded JSON object for upstream parameters not represented by the installed native settings type, exposed only by bindings that forward it. Bedrock Converse exposes its native `bedrock_additional_model_requests_fields` under the same bounds and reserved-field rules. The locked Google Generate Content binding exposes no arbitrary-body passthrough, so its schema omits `extra_body`. Unknown escape-hatch leaves are not assigned invented schemas or model-support guarantees. Known native settings remain available as named fields. If a native setting and an escape-hatch field address the same outbound parameter, the effective configuration is rejected rather than depending on SDK-specific precedence.

All settings entry paths enforce the same reserved request boundary. Settings cannot replace the selected model, select fallback model IDs, supply credentials or connection targets, or replace Harness-owned messages, instructions, tools, tool results, streaming mode, or structured-output schemas. This applies to native aliases and nested escape hatches, including model-changing presets or `extra_body.model`. Non-authentication headers are permitted only within the integration's safe header contract; authorization and routing-to-another-endpoint headers remain Provider-owned. Downstream provider selection for the same OpenRouter model is permitted. Validation of the constructed outbound request preserves this boundary even when a new upstream parameter is passed through `extra_body`.

Model `settings` are actual request defaults. Discovery `profile` and `limits` are read-only Provider information, never Model create or update fields. A displayed output limit describes upstream capacity; it does not send `max_tokens`. Users set request controls through `settings`. Pydantic AI and trusted integration code retain ownership of the effective native profile and message transformations.

### Settings precedence

Effective settings are merged at Run acceptance, in ascending precedence: saved Model defaults, Agent settings, and explicit Run settings overrides. Merging is by top-level setting key, matching native settings composition. A later value replaces the entire earlier value at that key, including an object or array; there is no recursive merge. An absent key inherits, and an explicit null is a value only where the setting schema permits it. For example, overriding `openrouter_provider` replaces that whole routing object, while setting `max_tokens` preserves it. An `extra_body` override likewise replaces that whole object.

In a Run override, an absent `settings` field inherits Agent settings, an empty object adds no overrides, and `settings: null` clears Agent settings for that Run so that Model defaults apply. Model PATCH replaces a supplied settings object as a whole; an empty object clears its defaults. To remove an Agent override permanently, save a new AgentRevision without that setting. These map semantics do not change the native meaning of null-valued individual settings.

Switching `model_key` uses the new Model's defaults and revalidates inherited Agent settings plus explicit Run overrides against that selection. Incompatible values fail rather than being discarded. Replacement attempts and successor operations that preserve an effective configuration reuse its final settings without reapplying current defaults or suggestions.

## Agent selection and Run snapshot

The [Agent Management contract](28-agent-management.md#agentconfig) owns `AgentModel` and its resolved and effective forms. Agent configuration supplies `model_key`, optional settings overrides, and Harness model characteristics. It contains no independently selectable calling API.

Agent Revision creation resolves `model_key` to the internal `model_id`, validates settings against the current Model selection, and retains the stable identity and Agent-authored overrides. It does not copy the Model's mutable API or defaults into the Revision. Invoking any Agent Revision resolves the latest enabled Model at Run acceptance, validates the resulting settings, and freezes the selection. A Model API change therefore affects new Runs, including invocations of older AgentRevisions; incompatible retained Agent settings cause an explicit acceptance failure.

Run acceptance freezes Model identity and the fields that determine outbound request selection:

```python
class ModelExecutionSnapshot:
    schema_version: Literal["1"]
    model_id: ModelId
    model_key: str
    upstream_model: str
    model_api: str


class ModelExecutionObservation:
    model_id: ModelId
    model_key: str
    upstream_model: str
    model_api: str
```

The Provider ID is not duplicated in this snapshot because `Model.provider_id` is immutable. Runtime resolves the Provider through the retained `model_id`. The snapshot contains no profile, limits, endpoint, Provider config, credential, or secret. Profile and limits remain transient Provider information; they neither override the native Pydantic AI Model profile nor become Run reproducibility facts. Replacement attempts and explicit Retry reuse the same Model snapshot, so a mid-Run Model edit does not change upstream model or selected API.

The final merged non-secret settings are stored once in `EffectiveAgentConfig.model.settings`, alongside the execution snapshot and Harness model characteristics. Every outbound request uses those effective settings. Model edits and description refreshes do not alter them during the Run. Description schemas and advisory parameter support are not execution snapshots. Retained Runs are reconstructed from their explicit execution selection and settings; compatibility handling must not infer an API from current Model defaults or rewrite historical selections.

Provider values have different semantics. Immediately before every outbound model request, runtime:

1. reads the Model's immutable Provider relationship and current Model/Provider enabled state in a short database session;
2. copies the current Provider configuration and encrypted credential material;
3. closes the database session;
4. authenticates/decrypts and validates the endpoint; and
5. constructs the native Pydantic AI Model for the exact snapshotted calling API.

Provider edits therefore affect the next outbound request, including a later request within the same Run or a replacement attempt. This applies to credential rotation, authentication mode, endpoint, region, project, and API version. An HTTP request already dispatched is not altered or cancelled.

Disabling either the Model or its Provider is a live kill switch: the next outbound request fails closed before dispatch, including a retry in the same Run. Re-enabling permits subsequent requests. Runtime never falls back to another Provider, Model, or calling API.

## Native construction and endpoint safety

Pydantic AI owns provider invocation, message conversion, streaming, tool calls, structured-output protocol behavior, and the effective native Model profile. Foundation keeps Provider integration and calling-API selection as two finite trusted registries rather than repeating protocol selection inside every Provider type.

One Provider integration owns only:

- Provider config and credential validation;
- optional connection test and model discovery, plus model descriptions and safe parameter metadata mapping;
- endpoint derivation and policy validation; and
- construction of the native Pydantic AI Provider from the current connection state.

The calling-API registry maps each supported API key to its exact native Pydantic AI Model selection and serializable settings contract. Runtime first selects and constructs the current Provider integration, then applies the snapshotted calling-API binding and effective settings to the opaque upstream model name. Compatibility remains the finite `(provider_type, model_api)` allowlist declared by the Provider integration; it is not an allowlist of model IDs. Most bindings use Pydantic AI's native model inference; a small explicit constructor override is permitted only when inference cannot preserve the selected calling API exactly.

There is no Foundation `Interface`, `InterfaceAdapter`, or user-selectable adapter resource.

Every configurable or derived endpoint is validated immediately before dispatch:

- only `http` and `https` are accepted;
- user information, fragments, and credential-bearing or sensitive query parameters are rejected;
- loopback, link-local, cloud-metadata, and non-allowlisted private destinations are denied by default;
- only deployment operators can allow private domains or CIDR ranges;
- DNS answers and redirects are revalidated; and
- official Provider endpoints remain adapter-owned.

Provider credentials and configuration are copied under authorization and database consistency, but no transaction or session remains open across decryption, DNS, provider discovery, remote metadata lookup, testing, or model I/O.

## Management API

Provider-type discovery is deployment-scoped and read-only:

```http
GET /api/v1/model-provider-types
```

Workspace Provider resources and commands use:

```http
GET   /api/v1/workspaces/{workspace_id}/model-providers
POST  /api/v1/workspaces/{workspace_id}/model-providers
GET   /api/v1/workspaces/{workspace_id}/model-providers/{provider_id}
PATCH /api/v1/workspaces/{workspace_id}/model-providers/{provider_id}
POST  /api/v1/workspaces/{workspace_id}/model-providers/{provider_id}/test
POST  /api/v1/workspaces/{workspace_id}/model-providers/{provider_id}/discover-models
POST  /api/v1/workspaces/{workspace_id}/model-providers/{provider_id}/describe-model
```

Workspace Model resources and commands use:

```http
GET   /api/v1/workspaces/{workspace_id}/models
POST  /api/v1/workspaces/{workspace_id}/models
GET   /api/v1/workspaces/{workspace_id}/models/{model_id}
PATCH /api/v1/workspaces/{workspace_id}/models/{model_id}
POST  /api/v1/workspaces/{workspace_id}/models/{model_id}/test
```

Provider and Model collections use cursor pagination with deterministic `updated_at desc, id desc` ordering. Provider filters include name, type, and enabled state. Model filters include name/key, `provider_id`, and enabled state.

`discover-models` takes no pagination input and returns one `items` array containing the complete Provider catalog, ordered by `upstream_model` ascending with duplicate IDs consolidated. This transient command result is not an ordinary paginated resource collection. The response has no `next_cursor`, enumeration identity, or persisted catalog snapshot. The service follows upstream pagination internally, allowing at most 100 upstream pages, 4 MiB per upstream response, 10,000 unique models, and 32 MiB of serialized discovery output, under the operation deadline. A bound or upstream failure returns a safe operation error rather than truncated results. Filtering non-generative entries does not stop upstream traversal. Unsupported discovery returns `model_discovery_unsupported`.

`describe-model` accepts `upstream_model` and an optional `model_api`, and returns one `ModelDescription` without persisting or invoking a model. This same operation serves manual IDs, discovered candidates whose selected API changes, and existing Model editors. It requires no discovery-result token or Model ID. It can inspect upstream metadata but never performs generative inference. Description works with local trusted defaults when optional metadata is unavailable, including an unconfigured credential; credential and endpoint errors remain visible through discovery, connection testing, or inference. The service never sends credentials to an endpoint that failed policy validation.

Create is synchronous and retains no separate idempotency record. Duplicate normalized Provider names return `409 model_provider_name_conflict`; duplicate normalized Model keys return `409 model_key_conflict`. All PATCH routes require `If-Match`; stale state returns `412 precondition_failed` and changes nothing.

Provider testing validates its current connection and authentication without requiring a Model request when a safe provider-native operation exists. Model testing uses the Model's saved upstream ID, single API, and default settings; it accepts no separate API selector. A successful list or description is not a successful Model test. Both tests run outside database transactions, retain no provider response or test resource, may consume quota, and record only a bounded audit event. Discovery and description have the same secret and transaction boundaries.

## Lifecycle, authorization, and audit

Foundation exposes disable/re-enable instead of hard delete for both resources. Provider disable blocks every dependent Model at the next outbound request. A Provider with dependent Models cannot be removed by storage maintenance. Model keys and Provider relationships cannot be reused through a public delete path.

`models.read` permits safe Provider-type, Provider, and Model reads and the read-only `describe-model` operation. `models.manage` is required for Provider and Model mutations, credential replacement, connection and Model tests, discovery enumeration, and lifecycle commands. Workspace Viewer receives the read surface; Workspace Builder and Admin receive both surfaces. Remote metadata results and caches retain the configured Provider's authorization boundary, and every discovery or description request reauthorizes access. Agent-scoped grants do not confer Workspace Model-management authority.

Create, update, credential replacement/removal, test, discovery, description, enable, and disable actions produce security audit records. Audit details contain identifiers and safe outcome codes only; they never contain plaintext credentials, encrypted credential material, authorization headers, raw provider errors, prompts, or model output.

Usage and execution observations retain Model identity and selected calling API. Provider identity and type are obtained through the Model's immutable relationship when needed for attribution. The Usage domain owns immutable measure records; Model Management owns current configuration.

## Failure rules

- Unknown Provider type or calling API fails validation.
- A Model API not allowed by its Provider type fails validation.
- Invalid native setting shapes, forbidden request overrides, and conflicting native/extra-body values fail with safe field-path errors.
- Missing required Provider credential prevents authenticated discovery, testing, or inference; description can still return local suggestions without making an authenticated request.
- Disabled Model or Provider fails before the next outbound request.
- Missing Model key, missing Provider, or a Model/Provider Workspace mismatch is concealed as not found where required by authorization policy.
- Unsupported native binding, invalid current Provider config, credential decryption failure, or endpoint-policy failure prevents dispatch.
- Discovery failure never invalidates or mutates saved Models.
- Missing catalog entries and unknown parameter support do not prevent manual creation or execution through an allowed binding.
- Optional metadata failure falls back to trusted descriptions without claiming that the upstream connection or model works.
- Upstream parameter rejection is surfaced without silently removing settings.
- Provider changes never cause automatic calling-API fallback.

These rules intentionally avoid a general connection graph, credential resource hierarchy, adapter marketplace, or arbitrary protocol-composition language. New supported combinations extend the finite trusted registry and its tests.
