# Models

An agent calls a **model**: a named upstream model of a **model provider** account, with the model API to call it through, its capabilities and optional pricing. Providers and models are either shared by the organization or confined to one workspace; see [scopes](resources.md#scopes).

## Model providers

In Console, open **Models → Add model → Connect a new provider**, or manage providers under **Workspace settings → Providers** (workspace) and **Organization settings → Providers** (shared). Through the API, create a provider in `/api/v1/organizations/{organization_id}/model-providers` as described in [Providers](resources.md#providers).

| Type                                                               | Model APIs (default first)                                                        |
| ------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| `openai`                                                           | `openai.responses`, `openai.chat_completions`                                     |
| `anthropic`                                                        | `anthropic.messages`                                                              |
| `google_gemini`, `google_vertex`                                   | `google.generate_content`                                                         |
| `azure_openai`                                                     | `openai.responses`, `openai.chat_completions`                                     |
| `aws_bedrock`                                                      | `bedrock.converse`, `bedrock_mantle.responses`, `bedrock_mantle.chat_completions` |
| `openrouter`                                                       | `openrouter.chat_completions`                                                     |
| `ollama`                                                           | `ollama.chat_completions`                                                         |
| `alibaba_model_studio`, `deepseek`, `moonshot`, `minimax`, `zhipu` | `openai.chat_completions`                                                         |
| `typesafe`                                                         | `typesafe.system_one`                                                             |

Each type's configuration and credential fields come from the Harness; `GET /api/v1/provider-types/model` returns them as JSON Schema. Most types take an optional `base_url` and an `api_key` credential. See [Harness models](../a13n-harness/models.md) and [model authentication](../a13n-harness/model-authentication.md) for each type's options.

A model provider may also carry up to 32 **extra request headers**, for gateways that route or bill by header. Header values are secrets: they are encrypted, never returned (views list only `header_names`), and edited per name in a `PATCH` (`"X-Team": "..."` sets a value, `null` removes it, omitted names are kept). Transport, authentication and protocol header names are refused.

## Add a model

In Console, open **Models → Add model**, choose the provider, then pick a model from the model catalog or enter its model ID. The catalog lists recent text models from [models.dev](https://models.dev) that the provider's type serves, one row per model, with a choice of IDs when the provider serves a model under several (such as Bedrock regions). A provider of the generic `openai` type also offers **Other models (compatible)**, for an OpenAI-compatible endpoint serving another vendor's model. Picking a model fills in its model ID, capabilities and prices, which you can change before saving. When models.dev has not been reachable since the Service started, Console says the catalog is unavailable; enter the model ID instead.

Through the API, `GET /api/v1/model-catalog` returns the catalog's `items` and its `status`: `ready`, `stale` (the last catalog, while models.dev cannot be reached) or `unavailable`. Each item's `ref`, `characteristics` and `pricing` are values to copy into a new model:

```sh
curl "$A13N_URL/api/v1/model-catalog" -H "Authorization: Bearer $A13N_API_KEY"
```

Create the model in `/api/v1/organizations/{organization_id}/models` with its `config`:

```json
{
  "workspace_id": "ws_...",
  "provider_id": "mprov_...",
  "key": "local-llama",
  "name": "Llama (local)",
  "description": "Served by the team's Ollama host",
  "config": {
    "model_name": "llama3.3",
    "model_api": "ollama.chat_completions",
    "characteristics": {"capabilities": ["image_understanding"], "context_window_tokens": 131072},
    "max_tokens": 4096
  }
}
```

- `key` is unique per provider; agents select models by ID.
- `config.model_name` is the upstream model name, and `config.model_api` must be one of the provider type's model APIs.
- `config.characteristics` declares the context window, context-management thresholds and `capabilities`: `image_understanding`, `video_understanding`, `audio_understanding`, and `document_understanding` for PDF documents. Optional `max_tokens`, `temperature` and `top_p` are defaults that an agent's `model.settings` can override.
- `pricing` prices the model's own calls in usage records; catalog items carry one to copy. The provider and model it names only record where the prices came from, so a catalog price copied for another endpoint's model ID still applies.
- `catalog_ref`, such as `{"provider": "openai", "model": "gpt-5.5"}`, optionally records the catalog item's `ref` the model started from. Console uses it for the model's icon and name; the Service never checks it against the catalog.
- `enabled: false` creates the model disabled.

A model spends its provider's credential, so creating a model or changing its `config` needs `write` on the provider as well; models of a shared provider are managed with organization-scope grants. A workspace provider serves only its own workspace's models.

Change a model with `PATCH` (`name`, `description`, `config`, `pricing`, `catalog_ref`, `enabled`) and its `If-Match`. Disable it with `{"enabled": false}`; models have no delete operation. Model-API-specific settings, such as reasoning effort, belong to the agent revision that uses the model (`model.settings`), where they are validated against the provider type's `settings_schemas`. These schemas leave out operator timeouts, upstream model selection and provider-account conversation state (such as `openai_previous_response_id`, `bedrock_inference_profile`, `openrouter_models` or auxiliary `openai_moderation` models), and server-side tools such as `openai_native_tools`, which `max_usage` cannot fully account for.

### Advanced request settings

Use **Advanced → Settings JSON** on a Model, or **Provider-specific settings** on an Agent. Both accept `extra_headers`; `openai.responses` and `openai.chat_completions` also accept `extra_body` for inference options the installed SDK does not yet know:

```json
{
  "extra_body": {"reasoning": {"effort": "future-effort"}},
  "extra_headers": {"x-experiment": "candidate"}
}
```

This Responses example illustrates passthrough, not an upstream-supported effort value. Chat Completions uses its own wire fields, such as `reasoning_effort`. Ordinary typed settings remain preferable when available. Raw values win over ordinary inference settings at the SDK's final shallow merge; a raw nested object replaces its generated counterpart rather than merging into it. Model selection, messages, tools, structured output and session state cannot be changed this way. Use native `openai_text_verbosity` instead of raw Responses `text`, whose container also carries structured output.

Omit either object to inherit its Model default; set it to `{}` to clear that default for the Agent or Run. A Run's settings replace the Agent's entire settings object, so `settings: {}` still inherits Model defaults. To clear both defaults, send `settings: {"extra_body": {}, "extra_headers": {}}`. `null` is not accepted for these objects. Model and Provider changes apply to later attempts, including resumed work; settings are checked again before requests.

Header values here are **readable configuration**. Put secrets in the Provider's credential or encrypted extra headers instead. Request settings cannot replace authentication, protocol, existing Provider header names or its session-affinity header, regardless of case.

### Gateway session affinity

Set **Session affinity** in the Provider's advanced connection settings to a header your gateway recognizes. The Service sends a stable Thread-derived UUID on every model path, including reviewers and media understanding. Continuing a Thread keeps the value; children and forks receive their own. It does not use the Service Session ID or Run ID, and clearing request header defaults does not disable it. The gateway must implement routing for that header; selecting a preset does not configure the gateway.

A model's `config.model_api` must still be one the deployment offers; checking its settings, such as when an agent revision is saved, answers `503 unavailable` with `{"dependency": "model_api:<api>"}` for one it no longer does.

## Media understanding

An agent whose model cannot read images, video or audio can delegate that to another model. Each workspace can set a default per media kind, and each agent can choose its own.

In Console use **Workspace settings → Media understanding**. Through the API, workspace administrators replace all three defaults at once with the workspace's `If-Match`:

```sh
curl -X PUT "$A13N_URL/api/v1/workspaces/$WORKSPACE/media-understanding-defaults" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" -H "If-Match: $WORKSPACE_ETAG" \
  -d '{"image": "mdl_...", "audio": null}'
```

An omitted or `null` kind has no default. Each model must be usable in the workspace and declare the matching capability (`image_understanding`, `video_understanding` or `audio_understanding`).

At execution, an agent's own `media_understanding` choice wins and must be usable, or the run fails. A workspace default that is no longer usable is skipped with a warning, and that media kind is unavailable to the run.

## Usage and prices

Every model call of a run is recorded as a usage record attributed to the model that made it, and priced by that model's pricing, with a snapshot of the pricing at that time. This holds when two models of one agent graph name the same upstream model, even through providers of the same type, and when the provider answers under another model name, such as a dated snapshot of an alias. See [usage](agents-and-runs.md#usage).
