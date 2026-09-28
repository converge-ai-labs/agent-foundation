# Models

An agent calls a **model**: an upstream model of a **model provider** account, with the model API to call it through, its capabilities and optional pricing. Providers and models belong to one [workspace](resources.md#workspaces), and agents select a model by its key.

## Model providers

In Console, open **Models → Add model → Connect a new provider**, or manage providers under **Workspace settings → Providers**. Through the API, create a provider in `/api/v1/model-providers` as described in [Providers](resources.md#providers).

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

In Console, open **Models → Add model**, choose the provider, then pick a model from the model catalog or enter its upstream model ID. The catalog lists recent text models from [models.dev](https://models.dev) that the provider's type serves, one row per model, with a choice of IDs when the provider serves a model under several (such as Bedrock regions). A provider of the generic `openai` type also offers **Other models (compatible)**, for an OpenAI-compatible endpoint serving another vendor's model. Picking a model fills in its upstream model ID, capabilities and prices, which you can change before saving. When models.dev has not been reachable since the Service started, Console says the catalog is unavailable; enter the upstream model ID instead.

Through the API, `GET /api/v1/model-catalog` returns the catalog's `items` and its `status`: `ready`, `stale` (the last catalog, while models.dev cannot be reached) or `unavailable`. Each item's `ref`, `characteristics` and `pricing` are values to copy into a new model:

```sh
curl "$A13N_URL/api/v1/model-catalog" -H "Authorization: Bearer $A13N_API_KEY"
```

Create the model in `/api/v1/models` with its `config`:

```json
{
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

- `provider_id` names a model provider of the workspace, whose credential the model spends.
- `key` identifies the model in its workspace: in paths such as `/api/v1/models/local-llama`, in agent configurations and in usage. It defaults to the provider's type and the upstream model name joined by `-`, lowercased, with any other character a [key](resources.md#common-conventions) cannot hold replaced by `-` (`ollama-llama3.3` for an `ollama` provider here); set it when that is already taken (`409 already_exists`). It never changes.
- `config.model_name` is the upstream model name, and `config.model_api` must be one of the provider type's model APIs.
- `config.characteristics` declares the context window, context-management thresholds and `capabilities`: `image_understanding`, `video_understanding`, `audio_understanding`, and `document_understanding` for PDF documents. Optional `max_tokens`, `temperature` and `top_p` are defaults that an agent's `model_settings` can override.
- `pricing` prices the model's own calls in usage records; catalog items carry one to copy. The provider and model it names only record where the prices came from, so a catalog price copied for another endpoint's upstream model ID still applies.
- `catalog_ref`, such as `{"provider": "openai", "model": "gpt-5.5"}`, optionally records the catalog item's `ref` the model started from. Console uses it for the model's icon and name; the Service never checks it against the catalog.
- `enabled: false` creates the model disabled.

Change a model with `PATCH /api/v1/models/{key}` (`name`, `description`, `config`, `pricing`, `catalog_ref`, `enabled`) and its `If-Match`, `"{key}:{version}"`. Disable it with `{"enabled": false}`; models have no delete operation. Model-API-specific settings, such as reasoning effort, belong to the agent revision that uses the model (`model_settings`), where they are validated against the provider type's `settings_schemas`. These schemas leave out transport and raw request options (`extra_body`, `extra_headers`, `timeout`), upstream model selection and provider-account conversation state (such as `openai_previous_response_id`, `bedrock_inference_profile` or `openrouter_models`), and server-side tools a provider bills and runs outside the request, such as `openai_native_tools`, which `max_usage` cannot see: the settings form simply does not offer them.

A model's `config.model_api` must still be one the deployment offers; checking its settings, such as when an agent revision is saved, answers `503 unavailable` with `{"dependency": "model_api:<api>"}` for one it no longer does.

## Media understanding

An agent whose model cannot read images, video or audio can delegate that to another model. Each workspace can set a default per media kind, and each agent can choose its own.

In Console use **Workspace settings → Media understanding**. Through the API, workspace administrators replace all three defaults at once, by model key, with the `ETag` that `GET /api/v1/media-understanding-defaults` returns:

```sh
curl -X PUT "$A13N_URL/api/v1/media-understanding-defaults" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" -H "If-Match: $MEDIA_ETAG" \
  -d '{"image": "gpt-5.5", "audio": null}'
```

An omitted or `null` kind has no default. Each model must be usable in the workspace and declare the matching capability (`image_understanding`, `video_understanding` or `audio_understanding`).

At execution, an agent's own `media_understanding` choice wins and must be usable, or the run fails. A workspace default that is no longer usable is skipped with a warning, and that media kind is unavailable to the run.

## Usage and prices

Every model call of a run is recorded as a usage record attributed to the model that made it, and priced by that model's pricing, with a snapshot of the pricing at that time. This holds when two models of one agent graph name the same upstream model, even through providers of the same type, and when the provider answers under another model name, such as a dated snapshot of an alias. See [usage](agents-and-runs.md#usage).
