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

Each definition includes `configuration_schema`, `credential_schema`, `supported_model_apis`, `default_model_api`, `model_api_labels`, `catalog_providers`, and a `settings_schemas` map keyed by calling API. Choose a registered type and follow its schemas. Use `model_api_labels` for UI text while retaining the stable API keys in requests. For example, OpenRouter uses an empty configuration object and a separate API key:

```http
POST /api/v1/workspaces/<workspace-id>/model-providers
Authorization: Bearer <foundation-token>
Content-Type: application/json

{
  "type": "openrouter",
  "name": "OpenRouter Production",
  "configuration": {},
  "credential": {"api_key": "<openrouter-api-key>"}
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

The [shared preset guide](../a13n-harness-ui/models-and-authentication.md#gateway-session-affinity) describes LiteLLM, Conversation ID, Bifrost API-key affinity, and the legacy `x-session-id` choice. Presets store a concrete, freely editable name; they do not configure gateway routing. Verify affinity using gateway target information, not a successful connection test. Ordinary connection probes do not invent a persistent session.

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
  "credential": {"api_key": "<deepseek-api-key>"},
  "extra_headers": {"x-team": "research", "x-gateway-key": "<gateway-key>"}
}
```

All header values are encrypted with the Provider credential; reads return only their names in `header_names`. On PATCH, omitted names retain their values, strings replace them, and null deletes them. For example, `{"extra_headers": {"x-gateway-key": "<replacement>"}}` rotates only the gateway key. In Console, leave a saved value empty to keep it. Removing a row deletes that header when saved.

Headers apply to inference and supported connection tests. Names are case-insensitive and must be unique. Each Provider accepts up to 32 extra headers with names up to 128 characters and printable ASCII values up to 2048 characters. Transport-managed and native authentication headers are reserved. Use the primary credential and authentication controls for native authentication.

Choose **OpenAI** (`openai`) for either official OpenAI or a custom OpenAI-compatible endpoint. It supports `openai.responses` (the default) and `openai.chat_completions`. Set `base_url` without `/responses` or `/chat/completions`; Console offers an explicit correction when a full API URL is pasted. Authentication defaults to bearer and also supports `auth_mode: "none"` with no credential, or `auth_mode: "api_key_header"` with `api_key_header_name` and a separate credential. The endpoint must implement the API selected on each Model. A `/models` route is not required; a saved Model test checks inference.

Ollama requires its server URL. Azure accepts a resource endpoint or a custom base URL and retains its API-version rules. Vertex requires project and location even with a custom URL. Bedrock has separate Converse and Mantle URL overrides; the Mantle SDK chooses `/v1` or `/openai/v1` according to the model, preserving a gateway path prefix. All overrides remain subject to the Service's endpoint policy.

MiniMax uses its OpenAI-compatible Chat Completions API at `https://api.minimax.io/v1` by default. For a China account, set the Provider's `base_url` to `https://api.minimaxi.com/v1`. Its native catalog shows the standard MiniMax channel; the separate Token Plan directory channels are not included.

## Choose a catalog model or enter an ID

Choose a Provider, then a model from the public models.dev directory. The catalog selection fills the upstream ID, capabilities, context window, and reference prices. Edit the upstream ID if your endpoint uses a deployment name or gateway alias; the catalog reference remains unchanged. Review the API: OpenAI-compatible endpoints may implement Chat Completions without Responses.

```http
GET /api/v1/workspaces/<workspace-id>/model-catalog
Authorization: Bearer <foundation-token>
```

The collection reports `ready`, `stale` (last-good data after a refresh failure), or `unavailable`. A custom model can always be entered manually. No Provider model discovery or alias guessing is performed, and neither creation nor execution depends on the catalog being online.

The default global release cutoff is April 23, 2026, inclusive. Operators can change it:

```toml
[models]
catalog_released_since = "2026-04-23"
```

The public directory refreshes after one hour, with a one-minute retry after failure. New eligible entries appear without a code release. The filter excludes missing release dates, unsupported directory channels and non-text-generative entries; it is not an execution allowlist. Saved models survive directory changes.

The model picker starts with the selected Provider's own catalog, even with a custom endpoint. Each recognized model appears once; when the Provider has several regional or deployment variants, choose a **Provider model variant** to fill its exact upstream ID and prices. Different model versions remain separate.

Only OpenAI has an **Other models (compatible)…** entry. This opens other model identities for use through an OpenAI-compatible gateway; selecting one does not make it available on OpenAI's official endpoint. Enter the gateway's upstream ID. Chat Completions is selected initially; change the API if your gateway supports another binding. The selected channel's catalog price is used when available; otherwise the editor falls back to the model's official catalog price. Both are editable references, and a gateway may charge differently. Selecting a catalog model immediately applies its reference, upstream ID where known, capabilities, context window, and prices to the draft. Refresh never overwrites edits.

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
  "upstream_model": "openai/gpt-5.5",
  "catalog_ref": {"provider": "openrouter", "model": "openai/gpt-5.5"},
  "model_api": "openrouter.chat_completions",
  "settings": {"thinking": "high", "max_tokens": 1024},
  "declarations": {
    "supports_tools": true,
    "capabilities": ["image_understanding"],
    "context_window_tokens": 128000,
    "structured_output": true,
    "pricing": {"tiers": [
      {"above": null, "rates": {"input": "5", "output": "30", "cache_read": "0.5"}},
      {"above": 200000, "rates": {"input": "10", "output": "45", "cache_read": "1"}}
    ]}
  }
}
```

Use the exact invocation ID accepted by the endpoint. `catalog_ref` is optional; omit it or set it to null for a custom model. The API is required. Create stores submitted values without fetching metadata or inferring an identity.

The editor exposes Model request defaults through a JSON object, validated against the selected Provider and API. There are no dedicated Thinking effort or Max output tokens controls on the Model. Agent and Run settings can override Model defaults. Tool support, input capabilities, context window, structured output, and prices are editable.

Prices are USD per million tokens. The base row has `above: null`; later thresholds must be unique and ascending. Input length selects one row for the entire request, strictly above the threshold. Each row supplies complete prices without inheriting from another row. Blank/null means unknown; zero means free. Cache tokens count toward the input-length threshold but are charged at cache rates, not charged again as uncached input. Unsupported catalog conditions are shown as a warning rather than flattened.

Accepted Runs retain their saved prices, including each child's separate Model prices. These are reference estimates, not provider invoices. Audio pricing and incomplete used dimensions cannot be valued by this table.

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

The Provider type's JSON Schema describes the serializable native parameters for the selected API, including provider-specific settings. Missing parameter help text does not prevent configuration or execution. Unknown top-level keys, invalid value shapes, and explicitly supplied protected fields for model identity, credentials, endpoints, messages, tool declarations, or output schemas are rejected. Local validation does not guarantee upstream acceptance.

Use `extra_body` only when the schema exposes it. Bedrock Converse uses `bedrock_additional_model_requests_fields`; Google Generate Content has no arbitrary-body field in this binding. These fields allow arbitrary upstream extensions. You are responsible for whether those extensions work, including their precedence when they overlap native settings; Service does not guess vendor-specific types or reject those overlaps. It protects the calling API's explicit request-control paths, unified thinking controls, and Provider-owned connection fields. For example, `extra_body.tool_choice` cannot override Harness tool control, and Responses protects `text.format` while permitting `text.verbosity`. Unrelated nested data with the same field names remains allowed. Settings are limited to 64 KiB of UTF-8 JSON and 16 container levels, including after merging. Parameter errors include a safe field path without echoing the submitted value.

OpenAI shallow-merges `extra_body`. Supplying `text={"verbosity": "low"}` replaces the entire generated container, removing its structured-output `format`; empty objects do the same. Use `openai_text_verbosity` to retain the Harness-generated output format. Native thinking enablement and effort aliases are unavailable, and equivalent body override paths are reserved, including Anthropic `thinking` and the whole `output_config` container.

In Agent configuration, the `model` field selects the Workspace key and optional overrides:

```json
{"model": {"model_key": "primary", "settings": {"temperature": 0.2}}}
```

This is a fragment of Agent configuration; the other required Agent fields still apply. There is no separate `model_api` selection on the Agent or Run. At Run acceptance, the service merges Model defaults, Agent settings, and Run overrides in that order. Each later top-level key replaces the earlier value completely, including objects such as `openrouter_provider`.

Use only `thinking` to control thinking enablement and effort: `true`, `false`, or `minimal`, `low`, `medium`, `high`, or `xhigh`. It follows the same Model → Agent → Run precedence as other settings. An absent value inherits; when absent at every layer, the native default applies. Pydantic AI handles model support, effort mapping, and native request construction. Unsupported models ignore this setting, and always-thinking models ignore `false`. Existing native enablement and effort settings must be replaced with `thinking`; Service does not convert them automatically.

For Model defaults `{"temperature": 0.3, "max_tokens": 1024}` and Agent settings `{"temperature": 0.2}`:

| Run `config_override.model`          | Effective settings                         |
| ------------------------------------ | ------------------------------------------ |
| No settings override                 | `{"temperature": 0.2, "max_tokens": 1024}` |
| `{"settings": {}}`                   | `{"temperature": 0.2, "max_tokens": 1024}` |
| `{"settings": null}`                 | `{"temperature": 0.3, "max_tokens": 1024}` |
| `{"settings": {"temperature": 0.5}}` | `{"temperature": 0.5, "max_tokens": 1024}` |

Selecting another Model key uses its defaults and revalidates the inherited Agent settings. Accepted Runs retain their API, upstream ID, catalog reference, and merged settings across replacement attempts. Later Model edits affect new Runs. Provider credentials and connection configuration are resolved afresh for each outbound request, including within an existing Run. A matching catalog reference supplies the native Provider's profile using the reference model ID, independently of the gateway alias. Supported OpenAI-compatible families can also contribute their native Chat profile through an OpenAI connection. Cross-protocol references never transplant profile behavior; custom models use native upstream-name inference.

Model declarations also compose at acceptance. The Model supplies media capabilities and its base context window; Agent configuration may override context-window and context-management thresholds but cannot invent media support. The same composition applies to the primary Model, reviewer Model, and every nested Agent. Accepted Runs retain the resulting complete Harness characteristics even if the Model is edited later.

## Request retries

Subagents can use a different Model and different settings from their parent. Parent Run acceptance freezes each child's Model selection and merged settings together with the full child graph. Inline execution and independent asynchronous child Runs both use those accepted values.

Every outbound inference attempt checks the current Model and Provider state. Credential rotation therefore applies to the next attempt, and disabling either resource stops it. Service retries HTTP 429 and 503 responses up to three total attempts, respecting `Retry-After` up to 30 seconds. Other failures and streams already handed to Harness are not automatically replayed. Non-streamed completion and streamed connection setup, including retry waits, have a 600-second deadline by default; set `settings.timeout` to change it. After stream handoff, native transport timeouts and Run cancellation govern consumption. Bedrock Converse uses blocking SDK calls with a 5-second connect timeout and a 600-second read timeout; cancellation and shorter Service deadlines can only take effect when the current SDK call returns. Model tests follow the same rules under their shorter command deadline.

Installed providers publish structured credential schemas and authentication rules. Console renders nested fields and preserves their JSON types. Required, optional, or forbidden credentials can depend on configuration defaults and choices. On update, omission keeps the saved credential; null removes it when the resulting connection permits absence. AWS and Google service-account credentials use objects with their native fields, not JSON strings.

Accounts saved with the former string credential shape need an explicit credential replacement before inference. That PATCH preserves the Provider ID and existing extra headers; it does not interpret or reuse the discarded primary value. No database reset is needed.
