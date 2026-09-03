# Model Management

## Design Position

Foundation exposes two Workspace resources for primary generative-model execution:

- a `ModelProvider` is one configured account or endpoint; and
- a `Model` is one stable Workspace model alias owned by exactly one Provider.

Provider types and calling APIs are deployment registry values, not resources. A Workspace can create any number of Providers of the same type, such as two OpenAI accounts or two Ollama servers. Credentials, endpoint settings, region, project, and authentication mode belong to the Provider. Upstream model identity and supported calling APIs belong to the Model.

Neither resource has a version or immutable revision. Both are mutable under strong ETag preconditions. An Agent stores `model_key` plus one explicit `model_api`; each Run resolves the latest enabled Model when accepted and retains a Model execution snapshot. Provider configuration is deliberately not frozen: every outbound model request resolves the Model's current Provider configuration and credential.

This contract covers only the primary text or multimodal generative model used by an Agent. Embedding, reranking, moderation, speech, image generation, video generation, and other specialized model resources are outside this domain.

## Boundaries and vocabulary

| Concept            | Meaning                                                                                  | Durable resource |
| ------------------ | ---------------------------------------------------------------------------------------- | ---------------- |
| Provider type      | Trusted implementation family such as `openai`, `openrouter`, `ollama`, or `aws_bedrock` | No               |
| Model Provider     | One Workspace-owned configured account or endpoint                                       | Yes              |
| Calling API        | Request/response contract such as `openai.responses` or `anthropic.messages`             | Registry key     |
| Model              | Stable Workspace alias for one upstream model under one Provider                         | Yes              |
| Model API config   | Per-Model, per-calling-API profile and limits                                            | Model value      |
| Provider adapter   | Trusted management and construction code selected by Provider type                       | No               |
| Pydantic AI Model  | Process-local native model implementation used for a request                             | No               |
| Execution snapshot | Model fields retained by an accepted Run                                                 | Embedded value   |

An endpoint owner and a wire format are independent facts. An OpenRouter Provider can expose an OpenAI-compatible calling API without becoming an OpenAI Provider. An Ollama Provider can expose the same general format while retaining Ollama-specific discovery and authentication behavior. An OpenAI Provider can allow both Responses and Chat Completions, and one Model can declare either or both.

`ModelProvider` is shortened to Provider within this document when there is no ambiguity. It is not a load-balancing pool, failover policy, model, or generic secret container. `Model` is not an upstream catalog entry: it is a configured Workspace alias.

## Trusted Provider-type and calling-API registry

The distribution assembles a finite registry from trusted code. Public requests cannot register code, import a package, invent a calling API, or supply request transformations. Package installation alone grants no trust.

Each safe `ModelProviderTypeDefinition` exposes:

```python
class ModelProviderTypeDefinition:
    key: str
    display_name: str
    config_schema: JsonObject
    credential_schema: JsonObject
    supported_model_apis: tuple[str, ...]
    supports_model_discovery: bool
```

Schemas define accepted fields, bounds, defaults, and write-only credential input. They contain no credential values or operator-private configuration. Provider `type` selects one definition and is immutable after create.

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

Provider configuration contains endpoint and authentication mechanics but never a calling-API choice. A Model selects one or more API keys from its Provider type's allowlist. There is no default API and runtime never silently changes or falls back to another API.

Official direct Provider types use fixed or typed derived endpoints. The generic `openai_compatible` type accepts a bounded `base_url` and an explicit `none`, `bearer`, or named API-key-header authentication mode. Provider-specific values such as Azure resource endpoint/API version, Vertex project and location, Bedrock region, or Ollama base URL remain Provider configuration.

## Model Provider

A Provider has one opaque `ModelProviderId` with the `mprov` kind prefix:

```python
class ModelProvider:
    id: ModelProviderId
    workspace_id: WorkspaceId
    type: str
    name: str
    config: JsonObject
    credential_configured: bool
    enabled: bool
    created_by: PrincipalRef
    updated_by: PrincipalRef
    created_at: datetime
    updated_at: datetime
```

`name` is a human-readable, case-insensitively unique name within the Workspace. Provider type is not unique: `OpenAI Production` and `OpenAI Personal` can both have `type="openai"`.

Provider create and update accept a provider-schema-specific write-only `credential` field. Reads return only `credential_configured`; they never return plaintext, ciphertext, credential shape, masked suffixes, or a reusable Secret identifier. Omitting `credential` on update retains the current value. Supplying null removes it only when the Provider type permits an unauthenticated connection. Supplying another value atomically replaces it.

Foundation protects Provider credentials with the shared managed-secret encryption primitive and deployment key, but a Provider credential is not a public `Secret` resource. Model Provider and Environment Provider implementations can reuse that internal cryptographic storage boundary without exposing generic Secret selection in either resource API.

Provider `config`, credential, name, and enabled state are mutable. Provider `type` is immutable. Every update is atomic, audited, and requires the current strong ETag. Provider configuration has no revision number, compatibility snapshot, or historical read API.

## Model

A Model has one opaque internal `ModelId` and one memorable external `key`:

```python
class ModelProfile:
    input_modalities: tuple[Literal["text", "image", "audio", "video"], ...]
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


class ModelApiConfig:
    api: str
    profile: ModelProfile
    limits: ModelLimits


class Model:
    id: ModelId
    workspace_id: WorkspaceId
    key: str
    provider_id: ModelProviderId
    name: str
    description: str | None
    upstream_model: str
    model_apis: tuple[ModelApiConfig, ...]
    enabled: bool
    created_by: PrincipalRef
    updated_by: PrincipalRef
    created_at: datetime
    updated_at: datetime
```

`key` is normalized and case-insensitively unique within the Workspace. It is the identifier accepted by public Agent configuration, API, and SDK surfaces. The opaque `id` is retained for internal relationships and observations. `key` and `provider_id` are immutable; changing account or endpoint means creating another Model. This preserves the invariant that a Model always belongs to the same Provider.

`upstream_model` is a bounded opaque string passed to the selected native Pydantic AI Model. It is not restricted to a bundled or discovered catalog. This lets a Workspace use a newly released OpenAI, Anthropic, Gemini, or other upstream model before Foundation's catalog metadata is updated.

`model_apis` is a non-empty set with unique API keys. Each key must be allowed by the selected Provider type. Profile and limits are stored per API because one upstream model can expose different effective behavior through different calling APIs. These values are user-configurable authoring and observability metadata. Unknown facts remain null rather than false.

The safe profile deliberately mirrors only serializable Pydantic AI `ModelProfile` facts useful to users. Schema transformers, Python types, system-prompt templates, request transforms, and other execution mechanics remain trusted adapter/runtime code. Profile metadata does not automatically gate Agent save or execution; the upstream response remains authoritative.

Model name, description, upstream model, API configs, and enabled state are mutable under a strong ETag. A Model has no version, revision, revision route, historical configuration API, or copy route.

## Discovery and manual Model creation

Discovery is a Provider-scoped management adapter operation:

```python
class DiscoveredModel:
    upstream_model: str
    display_name: str | None
    suggested_model_apis: tuple[ModelApiConfig, ...]
```

The adapter can implement `list_models` and optional model inspection using the Provider's current endpoint and credential. Results are transient, advisory, and may be briefly cached. They are not durable resources, do not create Models, do not update existing Models, and never become an allowlist.

Callers choose discovered entries to create ordinary Models. They can also create a Model manually under any Provider by supplying an arbitrary bounded `upstream_model`, an explicit non-empty API set, and profile/limit metadata. Catalog or discovery suggestions are only prefill. Provider type validation still rejects unsupported calling APIs.

Automatic profile or limit inspection follows the same rule: it produces suggestions, never authoritative runtime truth and never an implicit mutation.

## Agent selection and Run snapshot

Agent configuration selects a Model with both identifiers required:

```python
class AgentModel:
    model_key: str
    model_api: str
    settings: JsonObject
    characteristics: HarnessModelCharacteristics
```

Agent Revision creation resolves `model_key` to the internal `model_id`, validates that `model_api` is configured, and retains the resolved identity. It does not freeze the Model configuration. Invoking any Agent Revision resolves the latest enabled Model at Run acceptance. A caller or SDK can auto-select an API before saving only when the Model exposes exactly one API; the stored Agent value is still explicit.

Run acceptance freezes:

```python
class ModelExecutionSnapshot:
    schema_version: Literal["1"]
    model_id: ModelId
    model_key: str
    upstream_model: str
    model_api: str
    profile: ModelProfile
    limits: ModelLimits


class ModelExecutionObservation:
    model_id: ModelId
    model_key: str
    upstream_model: str
    model_api: str
```

The Provider ID is not duplicated in this snapshot because `Model.provider_id` is immutable. Runtime resolves the Provider through the retained `model_id`. The snapshot contains no endpoint, Provider config, credential, or secret. Replacement attempts and explicit Retry reuse the same Model snapshot, so a mid-Run Model edit does not change upstream model, selected API, profile, or limits.

Provider values have different semantics. Immediately before every outbound model request, runtime:

1. reads the Model's immutable Provider relationship and current Model/Provider enabled state in a short database session;
2. copies the current Provider configuration and encrypted credential material;
3. closes the database session;
4. authenticates/decrypts and validates the endpoint; and
5. constructs the native Pydantic AI Model for the exact snapshotted calling API.

Provider edits therefore affect the next outbound request, including a later request within the same Run or a replacement attempt. This applies to credential rotation, authentication mode, endpoint, region, project, and API version. An HTTP request already dispatched is not altered or cancelled.

Disabling either the Model or its Provider is a live kill switch: the next outbound request fails closed before dispatch, including a retry in the same Run. Re-enabling permits subsequent requests. Runtime never falls back to another Provider, Model, or calling API.

## Native construction and endpoint safety

Pydantic AI owns provider invocation, message conversion, streaming, tool calls, and structured-output protocol behavior. Foundation's trusted Provider adapter owns only:

- Provider config and credential validation;
- optional connection test and model discovery;
- endpoint derivation and policy validation; and
- construction of the registry-selected native Pydantic AI Model.

There is no Foundation `Interface`, `InterfaceAdapter`, or user-selectable adapter resource.

Every configurable or derived endpoint is validated immediately before dispatch:

- only `http` and `https` are accepted;
- user information, fragments, and credential-bearing or sensitive query parameters are rejected;
- loopback, link-local, cloud-metadata, and non-allowlisted private destinations are denied by default;
- only deployment operators can allow private domains or CIDR ranges;
- DNS answers and redirects are revalidated; and
- official Provider endpoints remain adapter-owned.

Provider credentials and config are copied under authorization and database consistency, but no transaction or session remains open across decryption, DNS, provider discovery, testing, or model I/O.

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

Create is synchronous and retains no separate idempotency record. Duplicate normalized Provider names return `409 model_provider_name_conflict`; duplicate normalized Model keys return `409 model_key_conflict`. All PATCH routes require `If-Match`; stale state returns `412 precondition_failed` and changes nothing.

Provider testing validates its current connection and authentication without requiring a Model request when a safe provider-native operation exists. Model testing accepts one explicit configured `model_api` and tests the exact `(Model, model_api)` combination. Both run outside database transactions, retain no provider response or test resource, may consume quota, and record only a bounded audit event. Discovery has the same secret and transaction boundaries.

## Lifecycle, authorization, and audit

Foundation exposes disable/re-enable instead of hard delete for both resources. Provider disable blocks every dependent Model at the next outbound request. A Provider with dependent Models cannot be removed by storage maintenance. Model keys and Provider relationships cannot be reused through a public delete path.

Workspace Viewer can read safe Provider-type, Provider, Model, and discovery metadata. Workspace Builder and Admin can create and update Providers and Models, replace credentials, test connections, discover models, and enable or disable resources. Agent-scoped grants do not confer Workspace Model-management authority.

Create, update, credential replacement/removal, test, discovery, enable, and disable actions produce security audit records. Audit details contain identifiers and safe outcome codes only; they never contain plaintext credentials, encrypted credential material, authorization headers, raw provider errors, prompts, or model output.

Usage and execution observations retain Model identity and selected calling API. Provider identity and type are obtained through the Model's immutable relationship when needed for attribution. The Usage domain owns immutable measure records; Model Management owns current configuration.

## Failure rules

- Unknown Provider type or calling API fails validation.
- A Model API not allowed by its Provider type fails validation.
- Missing required Provider credential fails closed.
- Disabled Model or Provider fails before the next outbound request.
- Missing Model key, missing Provider, or a Model/Provider Workspace mismatch is concealed as not found where required by authorization policy.
- Unsupported native binding, invalid current Provider config, credential decryption failure, or endpoint-policy failure prevents dispatch.
- Discovery failure never invalidates or mutates saved Models.
- Provider changes never cause automatic calling-API fallback.

These rules intentionally avoid a general connection graph, credential resource hierarchy, adapter marketplace, or arbitrary protocol-composition language. New supported combinations extend the finite trusted registry and its tests.
