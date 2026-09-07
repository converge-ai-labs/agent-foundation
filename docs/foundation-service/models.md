# Configure Models

A Model Provider stores an account or endpoint and its credential. A Model gives an upstream model a stable key within the models available to a Workspace, one calling API, and editable request defaults. Several Models can share a Provider or upstream ID while using different settings.

Models and Model Providers can belong to an Organization or a Workspace. Organization configuration is automatically available in every child Workspace. Organization Admin manages it through the corresponding `/api/v1/organizations/{organization_id}/models` and `/model-providers` routes using an Organization-scoped session. Workspace lists include both local and Organization resources; Workspace members can use shared configuration but cannot edit it. A Workspace Model can also use an Organization Provider.

Continue selecting models with a bare `model_key`. A key cannot be duplicated between an Organization and any of its Workspaces, or within one scope. Separate Workspaces may reuse a key. Conflicting creates return `409 model_key_conflict`, including concurrent creates.

Use the public HTTP API on a control-plane or all-in-one Foundation Service. The examples below use placeholders for Workspace and resource IDs. Authenticate each request with a Foundation credential authorized for that Workspace; Provider credentials belong only in the Provider's write-only `credential` field. Workspace Viewer can read configuration and request model descriptions. Workspace Builder or Admin can create, edit, discover, and test Models.

## Create a Provider

Read the available Provider types:

```http
GET /api/v1/model-provider-types
Authorization: Bearer <foundation-token>
```

Each definition includes `configuration_schema`, `credential_schema`, `supported_model_apis`, `default_model_api`, and `supports_model_discovery`. Choose a registered type and follow its schemas. For example, OpenRouter uses an empty configuration object and a separate API key:

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

## Discover candidates or enter an ID

If the Provider supports discovery, request the complete catalog:

```http
POST /api/v1/workspaces/<workspace-id>/model-providers/<provider-id>/discover-models
Authorization: Bearer <foundation-token>
```

The response contains one complete `items` array, ordered by upstream ID and deduplicated, plus a `settings_schemas` map keyed by calling API. Each item contains suggested configuration and metadata without repeating the schema. Use `settings_schemas[item.suggested_model_api]` to render its parameters; the map also includes the Provider's other supported APIs. Search and paginate these results in your client; repeat the request to refresh. Discovery accepts no `limit` or `cursor`, and returns no `next_cursor`.

Discovery creates no Models. A successful empty list, an upstream failure, and `model_discovery_unsupported` are different outcomes. The service follows upstream pages internally, with limits of 100 pages, 4 MiB per upstream response, 10,000 unique models, and 32 MiB of discovery output. Exceeding a bound returns an error, never a silently truncated catalog.

You can also describe any upstream ID directly, including a deployment or a newly released model absent from discovery:

```http
POST /api/v1/workspaces/<workspace-id>/model-providers/<provider-id>/describe-model
Authorization: Bearer <foundation-token>
Content-Type: application/json

{
  "upstream_model": "vendor/model-id",
  "model_api": "openrouter.chat_completions"
}
```

Omit `model_api` to use the suggested binding, or supply one of the Provider's allowed APIs. Each description includes `suggested_model_api`, `suggested_settings`, `profile`, `limits`, `settings_schema`, and `parameter_support`. Remote metadata failures still allow a local schema with unknown capability information. Description performs no inference and does not prove that the upstream ID or credential works.

Descriptions are suggestions. They never update saved Models or become a list of permitted upstream IDs. Manual creation works without discovery or description.

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
  "upstream_model": "vendor/model-id",
  "model_api": "openrouter.chat_completions",
  "settings": {"temperature": 0.3, "max_tokens": 1024}
}
```

Replace `vendor/model-id` with the exact invocation ID accepted by your endpoint. `settings` defaults to `{}`. To restrict OpenRouter's downstream providers for this same model, set native `openrouter_provider` settings, for example `{"only": ["<downstream-provider-id>"]}`. Use identifiers accepted by OpenRouter; Foundation does not select a different model or calling API as a fallback.

`profile` and `limits` appear only in discovery and description results as read-only Provider information. They are not Model create or update fields. For example, a discovered output limit of 32,000 describes upstream capacity; set `settings.max_tokens` to 8,000 to request a smaller output budget.

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

Omitting `settings` preserves existing defaults. Changing the upstream ID or API validates the resulting configuration and never silently removes incompatible settings. Refreshing a description also leaves saved values unchanged. Disable a Model or Provider with `{"enabled": false}`; disabling a Provider blocks every dependent Model at the next outbound request.

## Parameters and overrides

The returned JSON Schema describes the serializable native parameters for the selected API, including provider-specific settings. Missing parameter help text does not prevent configuration or execution. Unknown top-level keys, invalid value shapes, and explicitly supplied protected fields for model identity, credentials, endpoints, messages, tool declarations, or output schemas are rejected. `parameter_support` is advisory: unknown support permits manual configuration, and local validation does not guarantee upstream acceptance.

Use `extra_body` only when the schema exposes it. Bedrock Converse uses `bedrock_additional_model_requests_fields`; Google Generate Content has no arbitrary-body field in this binding. These fields allow arbitrary upstream extensions. You are responsible for whether those extensions work, including their precedence when they overlap native settings; Foundation does not guess vendor-specific types or reject those overlaps. It protects only the calling API's explicit request-control paths and Provider-owned connection fields. For example, `extra_body.tool_choice` cannot override Harness tool control, and Responses protects `text.format` while permitting `text.verbosity`. Unrelated nested data with the same field names remains allowed. Settings are limited to 64 KiB of UTF-8 JSON and 16 container levels, including after merging. Parameter errors include a safe field path without echoing the submitted value.

OpenAI and Anthropic shallow-merge `extra_body`. Supplying `text={"verbosity": "low"}` or `output_config={"effort": "low"}` passes validation but replaces the entire generated container, removing its structured-output `format`; empty objects do the same. This behavior is intentionally caller-owned. Use the native `openai_text_verbosity` or `anthropic_effort` setting to retain the Harness-generated output format.

In Agent configuration, the `model` field selects the Workspace key and optional overrides:

```json
{"model": {"model_key": "primary", "settings": {"temperature": 0.2}}}
```

This is a fragment of Agent configuration; the other required Agent fields still apply. There is no separate `model_api` selection on the Agent or Run. At Run acceptance, the service merges Model defaults, Agent settings, and Run overrides in that order. Each later top-level key replaces the earlier value completely, including objects such as `openrouter_provider`.

For Model defaults `{"temperature": 0.3, "max_tokens": 1024}` and Agent settings `{"temperature": 0.2}`:

| Run `config_override.model`          | Effective settings                         |
| ------------------------------------ | ------------------------------------------ |
| No settings override                 | `{"temperature": 0.2, "max_tokens": 1024}` |
| `{"settings": {}}`                   | `{"temperature": 0.2, "max_tokens": 1024}` |
| `{"settings": null}`                 | `{"temperature": 0.3, "max_tokens": 1024}` |
| `{"settings": {"temperature": 0.5}}` | `{"temperature": 0.5, "max_tokens": 1024}` |

Selecting another Model key uses its defaults and revalidates the inherited Agent settings. Accepted Runs retain their API, upstream ID, and merged settings across replacement attempts. Later Model edits affect new Runs. Provider credentials and connection configuration are resolved afresh for each outbound request, including within an existing Run.

## Request retries

Every outbound inference attempt checks the current Model and Provider state. Credential rotation therefore applies to the next attempt, and disabling either resource stops it. Foundation retries HTTP 429 and 503 responses up to three total attempts, respecting `Retry-After` up to 30 seconds. Other failures and streams already handed to Harness are not automatically replayed. Non-streamed completion and streamed connection setup, including retry waits, have a 600-second deadline by default; set `settings.timeout` to change it. After stream handoff, native transport timeouts and Run cancellation govern consumption. Bedrock Converse uses blocking SDK calls with a 5-second connect timeout and a 600-second read timeout; cancellation and shorter Foundation deadlines can only take effect when the current SDK call returns. Model tests follow the same rules under their shorter command deadline.
