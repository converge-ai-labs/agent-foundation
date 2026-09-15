# Configure Models

A Model Provider stores an account or endpoint and its credential. A Model gives an upstream model a stable key within the models available to a Workspace, one calling API, and editable request defaults. Several Models can share a Provider or upstream ID while using different settings.

Models and Model Providers can belong to an Organization or a Workspace. Organization configuration is automatically available in every child Workspace. Organization Admin manages it through the corresponding `/api/v1/organizations/{organization}/models` and `/model-providers` routes using an Organization-scoped session. Workspace lists include both local and Organization resources; Workspace members can use shared configuration but cannot edit it. A Workspace Model can also use an Organization Provider.

Continue selecting models with a bare `model_key`. A key cannot be duplicated between an Organization and any of its Workspaces, or within one scope. Separate Workspaces may reuse a key. Conflicting creates return `409 model_key_conflict`, including concurrent creates.

Use the public HTTP API on a control-plane or all-in-one a13n Service. The examples below use placeholders for Workspace and resource IDs. Authenticate each request with a Service credential authorized for that Workspace; Provider credentials belong in the write-only `credential` and `extra_headers` fields. Workspace Viewer can read configuration. Workspace Builder or Admin can create, edit, discover, and test Models.

## Create a Provider

Read the available Provider types:

```http
GET /api/v1/model-provider-types
Authorization: Bearer <foundation-token>
```

Each definition includes `configuration_schema`, `credential_schema`, `supported_model_apis`, `default_model_api`, `model_api_labels`, `supports_model_discovery`, and a `settings_schemas` map keyed by calling API. Choose a registered type and follow its schemas. Use `model_api_labels` for UI text while retaining the stable API keys in requests. For example, OpenRouter uses an empty configuration object and a separate API key:

```http
POST /api/v1/workspaces/<workspace-id>/model-providers
Authorization: Bearer <foundation-token>
Content-Type: application/json

{
  "type": "openrouter",
  "name": "OpenRouter Production",
  "configuration": {},
  "credential": "<openrouter-api-key>"
}
```

Keep the returned Provider `id`. Reads expose `credential_configured`, never the credential itself. A successful save validates configuration; use the Provider's `/test` endpoint to check its current connection and authentication. A list-based test reads only the first page. If the integration has no safe Provider-level probe, the result is `connection_test_unsupported`; test a saved Model to check inference instead.

### Gateway session affinity

Configure **Provider → Advanced settings → Gateway session affinity** in Console. Choose a preset, or type a custom replacement in **Session affinity header**. Models display the inherited setting and link back to Provider management; there is no per-Model override.

The HTTP field belongs to the Provider's `configuration`, next to its endpoint:

```json
{
  "configuration": {
    "base_url": "https://gateway.example.com/v1",
    "session_affinity_header": "x-litellm-session-id"
  }
}
```

Only the **name** is configured. Service supplies a stable UUID v5 derived from the current Harness Thread ID as its value on inference requests, using the [shared Harness derivation](../a13n-harness/models.md#automatic-model-request-affinity). The derived value is not persisted. Do not add a fixed session value to the secret `extra_headers` mapping or Model request settings. Static headers, authentication, and protocol fields cannot claim the selected affinity name.

The [shared preset guide](../a13n-harness-ui/models-and-authentication.md#gateway-session-affinity) describes LiteLLM, Conversation ID, Bifrost API-key affinity, and the legacy `x-session-id` choice. Presets store a concrete, freely editable name; they do not configure gateway routing. Verify affinity using gateway target information, not a successful connection test. Discovery and ordinary connection probes do not invent a persistent session.

Leave the field absent or set it to `null` to disable it. Provider updates replace `configuration`, so retain other connection fields when updating it. The next outbound attempt reads the current header together with the current endpoint, including retries within a Run. It keeps the same derived affinity value but stops sending the old header. Independent Threads, child Threads, and forks have distinct IDs. OpenAI prompt caching remains independently controlled.

**Affinity value upgrade:** automatic gateway headers and prompt-cache keys now use the derived UUID rather than the raw Thread ID. Existing Threads switch outbound values once, which may reset upstream cache or routing affinity. Internal Thread IDs and history remain unchanged; no state migration is needed.

**Upgrade note:** the previous implicit `x-session-id` default is removed. Explicitly select `session_affinity_header: x-session-id` on existing Providers that need it. No database migration or automatic Provider rewrite is performed. Service ignores the legacy global header switch; Provider connection configuration is authoritative, including for retained Runs.

### Custom endpoints and headers

Choose the native Provider type, such as DeepSeek or Anthropic, and expand **Advanced settings** in Console to override its base URL or add headers. Leaving the URL empty uses the Provider's default endpoint. Native authentication and model behavior are preserved.

For example, route DeepSeek through a gateway with a team header and a gateway key:

```json
{
  "type": "deepseek",
  "name": "DeepSeek Gateway",
  "configuration": {
    "base_url": "https://gateway.example.com/deepseek/v1"
  },
  "credential": "<deepseek-api-key>",
  "extra_headers": {"x-team": "research", "x-gateway-key": "<gateway-key>"}
}
```

All header values are encrypted with the Provider credential; reads return only their names in `header_names`. On PATCH, omitted names retain their values, strings replace them, and null deletes them. For example, `{"extra_headers": {"x-gateway-key": "<replacement>"}}` rotates only the gateway key. In Console, leave a saved value empty to keep it. Removing a row deletes that header when saved.

Headers apply to inference, discovery, and supported connection tests. Names are case-insensitive and must be unique. Each Provider accepts up to 32 extra headers with names up to 128 characters and printable ASCII values up to 2048 characters. Transport-managed and native authentication headers are reserved. Use the primary credential and authentication controls for native authentication.

Choose **OpenAI** (`openai`) for either official OpenAI or a custom OpenAI-compatible endpoint. It supports `openai.responses` (the default) and `openai.chat_completions`. Set `base_url` without `/responses` or `/chat/completions`; Console offers an explicit correction when a full API URL is pasted. Authentication defaults to bearer and also supports `auth_mode: "none"` with no credential, or `auth_mode: "api_key_header"` with `api_key_header_name` and a separate credential. The endpoint must implement the API selected on each Model. If it does not expose `/models`, enter the model ID manually and test the saved Model.

Ollama requires its server URL. Azure accepts a resource endpoint or a custom base URL and retains its API-version rules. Vertex requires project and location even with a custom URL. Bedrock has separate Converse and Mantle URL overrides; the Mantle SDK chooses `/v1` or `/openai/v1` according to the model, preserving a gateway path prefix. All overrides remain subject to the Service's endpoint policy.

## Discover candidates or enter an ID

If the Provider supports discovery, request the complete catalog:

```http
POST /api/v1/workspaces/<workspace-id>/model-providers/<provider-id>/discover-models
Authorization: Bearer <foundation-token>
```

The response contains one complete `items` array, ordered by upstream ID and deduplicated. Each item contains suggested configuration and advisory metadata without embedding a schema. Use the selected Provider type's `settings_schemas[item.suggested_model_api]` to render its parameters. Search and paginate these results in your client; repeat the request to refresh. Discovery accepts no `limit` or `cursor`, and returns no `next_cursor`.

Discovery creates no Models. A successful empty list, an upstream failure, and `model_discovery_unsupported` are different outcomes. The service follows upstream pages internally, with limits of 100 pages, 4 MiB per upstream response, 10,000 unique models, and 32 MiB of discovery output. Exceeding a bound returns an error, never a silently truncated catalog.

Discovery is optional authoring assistance, not a permitted-model list. You can manually create any upstream ID, including a deployment or a newly released model absent from discovery. Choose one of the Provider type's supported APIs and its corresponding static settings schema. A Model test, rather than catalog metadata, checks whether the saved ID and credential work.

For manual IDs, first inspect the base models supplied by the installed Pydantic AI version:

```http
GET /api/v1/base-models
Authorization: Bearer <foundation-token>
```

Each item contains the exact `base_model` name accepted on Model create, its inferred calling API when one is known, and a human-readable API label. A base model is an authoring reference: it supplies native profile behavior and may identify catalog declarations, but it never replaces the `upstream_model` sent to your Provider.

Ask the Service to match an upstream or relay name to that installed directory and return editable defaults:

```http
POST /api/v1/workspaces/<workspace-id>/model-catalog/suggestions
Authorization: Bearer <foundation-token>
Content-Type: application/json

{"provider_id": "<provider-id>", "upstream_model": "my-gpt-5"}
```

Matching uses only installed Pydantic AI names: exact names, separator-normalized names, then the longest complete name-token sequence. Numeric boundaries prevent names such as `gpt-5` from matching `gpt-51`. Identity matching happens before API selection, so a uniquely known identity is still returned with a null API when the Provider cannot infer a compatible one; creation then requires an explicit API. Declared equivalent namespaces collapse to one identity: OpenAI Responses and Chat variants use the Provider's ordered default API or an explicit API, while Google and Google Cloud variants share their common API. A native Provider retains its own equivalent namespace; a custom endpoint uses the canonical variant. Equal matches for genuinely distinct model identities remain ambiguous, and an exact `base_model` preserves the selected Pydantic AI name. The response source is `explicit`, `exact`, `normalized`, `name_tokens`, `ambiguous`, or `none` and includes every equally ranked distinct candidate when ambiguous.

After a base model is resolved, the cached models.dev catalog may enrich it with declarations. It never performs a second identity match. Actual-provider catalog entries may contribute that channel's price; custom endpoints use provider-independent facts and never borrow another channel's quote. Each single-flight refresh has a whole-operation deadline in addition to HTTP timeouts. Timeout or catalog failure returns the last-good snapshot, or empty declarations before the first success, and schedules retry from completion without blocking manual creation.

## Save and test a Model

Submit the configuration you want to retain:

```http
POST /api/v1/workspaces/<workspace-id>/models
Authorization: Bearer <foundation-token>
Content-Type: application/json

{
  "key": "primary",
  "provider_id": "<provider-id>",
  "name": "Primary Model",
  "upstream_model": "openai/gpt-5",
  "base_model": "openai-chat:gpt-5",
  "model_api": "openrouter.chat_completions",
  "settings": {"temperature": 0.3, "max_tokens": 1024},
  "declarations": {
    "thinking_efforts": ["low", "medium", "high"],
    "capabilities": ["image_understanding"],
    "context_window_tokens": 128000,
    "max_output_tokens": 32000,
    "structured_output": true,
    "pricing": {"input": 1.25, "output": 10, "cache_read": 0.125, "cache_write": null}
  }
}
```

Replace `openai/gpt-5` with the exact invocation ID accepted by your endpoint. `base_model` must be one exact entry from `GET /api/v1/base-models`. If it is omitted, create applies the same installed-directory inference used by the suggestion route; one unique match is saved and supplies `model_api` when that field is omitted. An ambiguous or absent match requires an explicit `model_api`. Explicit `base_model: null` suppresses inference. An explicit `model_api` always wins, including when it selects a different protocol from the base model. OpenAI Responses and Chat variants route the known GPT identity through the final OpenAI implementation; a reference from another native family remains useful for declarations but its incompatible native profile is not copied.

`settings` defaults to `{}`. Declarations have complete response defaults: empty effort choices and capabilities, null capacities and structured-output support, and null pricing. On create, a unique resolved base model supplies only omitted declaration fields from the cache; explicit empty arrays, nulls, false, zeros, and individual pricing fields win. These values remain editable and are never refreshed into a saved Model. To restrict OpenRouter's downstream providers for this same model, set native `openrouter_provider` settings, for example `{"only": ["<downstream-provider-id>"]}`. Use identifiers accepted by OpenRouter; Service does not select a different model or calling API as a fallback.

`profile` and `limits` appear only in discovery results as read-only Provider information. Catalog defaults copied into declarations remain advisory authoring facts. In particular, `max_output_tokens` describes capacity and never sends or maximizes `settings.max_tokens`; set `settings.max_tokens` separately to request a smaller output budget.

Test the saved configuration:

```http
POST /api/v1/workspaces/<workspace-id>/models/<model-id>/test
Authorization: Bearer <foundation-token>
Content-Type: application/json

{}
```

The test uses the saved upstream ID, API, and defaults. It can consume quota or incur cost. Inspect `success`, `code`, and `message` in the result; an HTTP success alone does not mean the model connection succeeded. No additional API selector is accepted.

## Edit safely

Read a Provider or Model to obtain its current `ETag`, then pass that exact header value as `If-Match` on PATCH. A stale ETag returns `412 precondition_failed` without applying the change. Model key and Provider relationship are immutable; create another Model to change either.

A supplied Model `settings` object replaces all saved defaults. For example, this clears them:

```http
PATCH /api/v1/workspaces/<workspace-id>/models/<model-id>
Authorization: Bearer <foundation-token>
If-Match: <exact-etag-from-get>
Content-Type: application/json

{"settings": {}}
```

Omitting `settings` or `declarations` preserves that existing field. Changing the upstream ID or API validates the resulting configuration and never silently removes incompatible settings. Disable a Model or Provider with `{"enabled": false}`; disabling a Provider blocks every dependent Model at the next outbound request.

## Parameters and overrides

The Provider type's JSON Schema describes the serializable native parameters for the selected API, including provider-specific settings. Missing parameter help text does not prevent configuration or execution. Unknown top-level keys, invalid value shapes, and explicitly supplied protected fields for model identity, credentials, endpoints, messages, tool declarations, or output schemas are rejected. A discovery candidate's `parameter_support` is advisory: unknown support permits manual configuration, and local validation does not guarantee upstream acceptance.

Use `extra_body` only when the schema exposes it. Bedrock Converse uses `bedrock_additional_model_requests_fields`; Google Generate Content has no arbitrary-body field in this binding. These fields allow arbitrary upstream extensions. You are responsible for whether those extensions work, including their precedence when they overlap native settings; Service does not guess vendor-specific types or reject those overlaps. It protects only the calling API's explicit request-control paths and Provider-owned connection fields. For example, `extra_body.tool_choice` cannot override Harness tool control, and Responses protects `text.format` while permitting `text.verbosity`. Unrelated nested data with the same field names remains allowed. Settings are limited to 64 KiB of UTF-8 JSON and 16 container levels, including after merging. Parameter errors include a safe field path without echoing the submitted value.

OpenAI and Anthropic shallow-merge `extra_body`. Supplying `text={"verbosity": "low"}` or `output_config={"effort": "low"}` passes validation but replaces the entire generated container, removing its structured-output `format`; empty objects do the same. This behavior is intentionally caller-owned. Use the native `openai_text_verbosity` or `anthropic_effort` setting to retain the Harness-generated output format.

In Agent configuration, the `model` field selects the Workspace key and optional overrides:

```json
{"model": {"model_key": "primary", "settings": {"temperature": 0.2}}}
```

This is a fragment of Agent configuration; the other required Agent fields still apply. There is no separate `model_api` selection on the Agent or Run. At Run acceptance, the service merges Model defaults, Agent settings, and Run overrides in that order. Each later top-level key replaces the earlier value completely, including objects such as `openrouter_provider`.

Unified `thinking`, native reasoning settings, and relevant reasoning fields inside an escape-hatch body are alternatives for the same semantic choice. A later layer that supplies one removes conflicting alternatives inherited from earlier layers while preserving unrelated settings and nested extension data. Supplying contradictory alternatives within one layer is rejected with a safe field path. The native model integration still owns effort translation and request construction.

For Model defaults `{"temperature": 0.3, "max_tokens": 1024}` and Agent settings `{"temperature": 0.2}`:

| Run `config_override.model`          | Effective settings                         |
| ------------------------------------ | ------------------------------------------ |
| No settings override                 | `{"temperature": 0.2, "max_tokens": 1024}` |
| `{"settings": {}}`                   | `{"temperature": 0.2, "max_tokens": 1024}` |
| `{"settings": null}`                 | `{"temperature": 0.3, "max_tokens": 1024}` |
| `{"settings": {"temperature": 0.5}}` | `{"temperature": 0.5, "max_tokens": 1024}` |

Selecting another Model key uses its defaults and revalidates the inherited Agent settings. Accepted Runs retain their API, upstream ID, base-model reference, and merged settings across replacement attempts. Later Model edits affect new Runs. Provider credentials and connection configuration are resolved afresh for each outbound request, including within an existing Run. When the saved base model has a profile-compatible final implementation—including an OpenAI GPT reference used through Responses or Chat Completions—each fresh request model uses the full Pydantic AI profile selected by that exact base-model name. This retains family and provider additions such as DeepSeek's OpenAI-compatible thinking wire behavior while preserving the saved upstream ID and current Provider endpoint and authentication. Cross-protocol references do not contribute a profile.

Model declarations also compose at acceptance. The Model supplies media capabilities and its base context window; Agent configuration may override context-window and context-management thresholds but cannot invent media support. The same composition applies to the primary Model, reviewer Model, and every nested Agent. Accepted Runs retain the resulting complete Harness characteristics even if the Model is edited later.

## Request retries

Subagents can use a different Model and different settings from their parent. Parent Run acceptance freezes each child's Model selection and merged settings together with the full child graph. Inline execution and independent asynchronous child Runs both use those accepted values.

Every outbound inference attempt checks the current Model and Provider state. Credential rotation therefore applies to the next attempt, and disabling either resource stops it. Service retries HTTP 429 and 503 responses up to three total attempts, respecting `Retry-After` up to 30 seconds. Other failures and streams already handed to Harness are not automatically replayed. Non-streamed completion and streamed connection setup, including retry waits, have a 600-second deadline by default; set `settings.timeout` to change it. After stream handoff, native transport timeouts and Run cancellation govern consumption. Bedrock Converse uses blocking SDK calls with a 5-second connect timeout and a 600-second read timeout; cancellation and shorter Service deadlines can only take effect when the current SDK call returns. Model tests follow the same rules under their shorter command deadline.
