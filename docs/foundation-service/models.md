# Configure Models

A Model Provider stores an account or endpoint and its credential. A Model gives an upstream model a stable Workspace key, one calling API, and editable request defaults. Several Models can share a Provider or upstream ID while using different settings.

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

Keep the returned Provider `id`. Reads expose `credential_configured`, never the credential itself. A successful save validates configuration; use the Provider's `/test` endpoint to check its current connection and authentication.

## Discover candidates or enter an ID

If the Provider supports discovery, request a page:

```http
POST /api/v1/workspaces/<workspace-id>/model-providers/<provider-id>/discover-models
Authorization: Bearer <foundation-token>
Content-Type: application/json

{"limit": 50}
```

The response contains `items` and `next_cursor`. To continue, submit the returned cursor with `limit`; stop when `next_cursor` is null. Limits range from 1 to 100. Results are ordered by upstream ID and deduplicated. If a cursor becomes invalid after a Provider or catalog change, start again without it.

Discovery creates no Models. An empty successful page, an upstream failure, and `model_discovery_unsupported` are different outcomes. Enumeration follows upstream pages within bounded budgets and returns an error if the catalog cannot be completely enumerated; it does not silently truncate results.

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

Omit `model_api` to use the suggested binding, or supply one of the Provider's allowed APIs. Each description includes `suggested_model_api`, `suggested_settings`, `suggested_profile`, `suggested_limits`, `settings_schema`, and `parameter_support`. Remote metadata failures still allow a local schema with unknown capability information. Description performs no inference and does not prove that the upstream ID or credential works.

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

Replace `vendor/model-id` with the exact invocation ID accepted by your endpoint. `settings` defaults to `{}`. Omitted profile and limit fields remain unknown. To restrict OpenRouter's downstream providers for this same model, set native `openrouter_provider` settings, for example `{"only": ["<downstream-provider-id>"]}`. Use identifiers accepted by OpenRouter; Foundation does not select a different model or calling API as a fallback.

`profile` and `limits` are descriptive metadata. Setting `limits.max_output_tokens` does not send a token limit; set `settings.max_tokens` for that request behavior. Suggested values are copied only when you explicitly submit them.

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

The returned JSON Schema describes the serializable native parameters for the selected API, including provider-specific settings. Unknown top-level keys, invalid value shapes, and attempts to replace model identity, credentials, endpoints, messages, tool declarations, or output schemas are rejected. `parameter_support` is advisory: unknown support permits manual configuration, and local validation does not guarantee upstream acceptance.

Use `extra_body` only when the schema exposes it. Bedrock Converse uses `bedrock_additional_model_requests_fields`; Google Generate Content has no arbitrary-body field in this binding. Escape-hatch fields obey the same reserved-field rules, and specifying the same outbound parameter through both a native setting and an escape hatch is rejected. Settings are limited to 64 KiB of UTF-8 JSON and 16 container levels, including after merging. Parameter errors include a safe field path without echoing the submitted value.

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

## Development API changes

Model requests now require one `model_api` instead of `model_apis`. Remove separate `model_api` fields from Agent configuration, Run overrides, and Model test requests. Existing callers must use the new request shapes; there are no legacy aliases.

The Model domain initialization migration directly creates the current single-API schema. This development change does not add a transitional migration or reject migration based on whether the Model table contains data.
