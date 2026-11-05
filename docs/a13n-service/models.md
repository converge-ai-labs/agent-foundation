---
title: Models
description: Connect a provider, give an agent a working model, and configure request defaults, media understanding, and prices.
---

Connect an upstream account once as a **model provider**, then save the **models** agents can use through it. The provider holds credentials and connection settings. Each model holds an upstream ID, calling API, input capabilities, request defaults, and optional prices. Both belong to one [workspace](resources.md#workspaces); an agent selects the model by its stable key.

Start with one model and one successful text response. You need workspace `write` permission, access to the provider account, and an endpoint reachable from the Service Worker. A catalog entry helps fill the form; a response verifies inference access. After that, tune [request settings](#advanced-request-settings) or add [media understanding](#media-understanding).

First [connect a model provider](model-providers.md#model-providers), then add a model below and test a short agent response. Provider-specific OAuth and gateway settings are in the reference, so you can return to them when needed.

## Add a model

**In Console:**

1. Open **Models → Add model** and choose the connected provider.
2. Select a catalog model or enter the upstream model ID. Choose the calling API your endpoint serves.
3. Review the name, input capabilities, request defaults, and prices. Catalog selection fills these values; you can change them before saving.
4. Save the enabled model, select it in an agent, and send a short text request. Continue with [agents and runs](agents-and-runs.md) once the agent returns a response.

The public catalog lists recent text models from models.dev, with one row per model and alternate IDs where needed, such as Bedrock regions. Generic `openai` providers also offer **Other models (compatible)** for another vendor's model behind an OpenAI-compatible endpoint. If the catalog is unavailable, enter the ID manually. For providers with account discovery, use the account choices rather than assuming the public catalog grants access.

**Through the API:** set `A13N_URL` to your Service origin and `A13N_API_KEY` to a [workspace API key](identity.md#api-keys) with `write` access. The following example assumes an enabled Ollama provider in that workspace, reachable from the Worker and serving `llama3.3`.

Save this body as `model.json`, replacing `mprov_...` with the provider's ID:

```json
{
  "provider_id": "mprov_...",
  "key": "local-llama",
  "name": "Llama (local)",
  "description": "Served by the team's Ollama host",
  "config": {
    "model_name": "llama3.3",
    "model_api": "ollama.chat_completions",
    "characteristics": {"context_window_tokens": 131072},
    "settings": {"max_tokens": 4096}
  }
}
```

Create it and read it back:

```sh
curl -fsS "$A13N_URL/api/v1/models" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  --data-binary @model.json
curl -fsS "$A13N_URL/api/v1/models/local-llama" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

Creation returns `201` and the saved model. Use `local-llama` in the agent's `config.model`, not `mprov_...` or `llama3.3`. Creating a model or changing its configuration also needs `write` on the provider, whose credentials it uses.

Choose the fields by their role:

| Field                                   | What you configure                                                                                                                                                                                                                                       |
| --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `key`                                   | Immutable workspace address used in agents, usage, and `/models/{key}`. If omitted, Service derives it from provider type and upstream name, lowercases it, and replaces disallowed character runs with `-`. Choose another key on `409 already_exists`. |
| `config.model_name`, `config.model_api` | Exact upstream ID and a calling API supported by this provider type                                                                                                                                                                                      |
| `config.characteristics`                | Context window, context-management thresholds, and actual input capabilities: `image_understanding`, `video_understanding`, `audio_understanding`, or `document_understanding` for PDFs                                                                  |
| `config.settings`                       | Request defaults for all uses of this model; agent settings may override them                                                                                                                                                                            |
| `pricing`                               | Prices for this model's own calls; copy catalog prices or supply your own                                                                                                                                                                                |
| `catalog_ref`                           | Optional `{provider, model}` provenance from the catalog, used by Console for identity display; checked for shape, not catalog membership                                                                                                                |
| `enabled`                               | Defaults to `true`; set `false` to save a disabled model                                                                                                                                                                                                 |

To seed a model from public metadata, query the catalog with exact, case-sensitive filters:

```sh
curl -fsS --get "$A13N_URL/api/v1/model-catalog" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  --data-urlencode "provider=openai" \
  --data-urlencode "model=gpt-5.5"
```

Copy the selected item's `characteristics`, `pricing`, and `ref` into the model's `config.characteristics`, `pricing`, and `catalog_ref`. The response status is `ready`, `stale` (the last catalog after a failed refresh), or `unavailable` (none fetched yet). Filters combine with AND; no match returns empty `items`. Omit `model` to list a channel, or both filters for the full catalog. Refreshes share the full catalog cache. Reading it never updates saved models or prices.

To edit a saved model, `GET /api/v1/models/{key}`, preserve its configuration, and `PATCH` with the returned ETag in `If-Match`. You can change `name`, `description`, `config`, `pricing`, `catalog_ref`, and `enabled`. Submit `config` as a complete replacement, including fields you want to keep. Disable with `{"enabled": false}`; models have no delete operation. [Resource editing](resources.md#common-conventions) shows the read–edit–check sequence.

### Image input preparation

Open the model's **Advanced → Image input** controls and choose how images enter requests:

| Control              | Default | Effect of a special value                                         |
| -------------------- | ------- | ----------------------------------------------------------------- |
| Prepare images       | On      | Off disables automatic preparation                                |
| Support GIF          | On      | Off removes binary GIFs; image URLs are not fetched or classified |
| Max images           | 20      | `0` removes every image                                           |
| Max image size (MiB) | 5       | `0` disables the byte limit                                       |

The size limit measures **base64-encoded bytes per image**, not source-file bytes; 1 MiB is 1,048,576 bytes. Through the API, use `config.characteristics.image_input`: omit it for defaults, supply an object to customize it, or set `null` to disable preparation. The [shared image policy](../a13n-harness/models.md#image-input-policy) lists all seven fields. Console preserves unexposed dimensions, splitting options, and exact byte budgets when you edit other controls.

The primary model, each child model, and the image-understanding model apply their own policy. Each attempt uses its resolved model configuration. Later attempts, including recovery, resolve current model defaults; Service does not freeze the model across attempts as Harness UI does.

### Advanced request settings

Tune a saved model in **Advanced**. Start with **Thinking effort** or **Max output tokens**; OpenAI Responses also exposes **Reasoning summary** and **Store response**. These controls and **Settings JSON** edit the same draft. Leave unsupported options unset, save, and test another message before relying on the change.

Use `config.settings` for model defaults and an agent's `model_settings` for that agent's overrides. For an OpenAI Responses model that supports medium reasoning, the settings object can be:

```json
{
  "openai_reasoning_effort": "medium",
  "extra_headers": {"x-experiment": "candidate"}
}
```

Settings apply in this order:

1. Service supplies its baseline: prompt caching is enabled; OpenAI Responses storage is off.
2. Model defaults replace matching baseline values. Native `config.settings` entries also replace same-named flat `config` defaults (`max_tokens`, `temperature`, `top_p`, `extra_body`, `extra_headers`).
3. Agent or reviewer settings replace matching model defaults. A run override first replaces the agent's **entire** `model_settings` object, then the model defaults are applied beneath it.

For `extra_body` and `extra_headers`, omission inherits the model object, an explicit object replaces it whole, `{}` clears it, and `null` is invalid. Thus run `model_settings: {}` removes agent overrides but still inherits model defaults. Use `model_settings: {"extra_body": {}, "extra_headers": {}}` to clear both request objects. Nested objects are never recursively merged.

Prefer typed parameters. For inference options the installed SDK does not yet recognize, `openai.responses` and `openai.chat_completions` accept `extra_body`; raw values override generated inference fields in a shallow merge. They cannot change the selected model, messages, tools, structured output, transport, or provider session state. Use `openai_text_verbosity`, not raw Responses `text`. The provider type's `settings_schemas` validates both model and agent settings and excludes operator timeouts, account state, alternate model selection, and server tools whose costs `max_usage` cannot fully track.

**Caching:** use `cache: false` to disable library-managed caching or a supported retention value to select its lifetime. Anthropic Messages defaults also enable `anthropic_cache_instructions`, `anthropic_cache_tool_definitions`, and `anthropic_cache_messages`, placing markers on content blocks for gateway compatibility. An explicit unified `cache` value or provider cache setting, including `anthropic_cache`, preserves the authored policy. Pydantic AI places the provider-specific markers; cache hits and the provider's implicit caching remain upstream behavior.

For 12-hour caching on a supported `openai.responses` model, set `{"cache": "12h"}` in **Settings JSON** (`config.settings`), or use `{"cache": {"retention": "12h"}}`. Agent/run overrides use the same shape under `model_settings` and follow the composition rules above. For a compatible gateway with an unrecognized model alias, use `{"openai_prompt_cache_options": {"mode": "implicit", "ttl": "12h"}}` instead. Use the typed setting, not `extra_body.prompt_cache_options`.

Unified translation depends on the installed model profile: Chat Completions downgrades 12h to 30m, and an unrecognized alias can leave unified caching unapplied. Explicit `openai_prompt_cache_options` or `openai_cache_instructions` settings take precedence, including `openai_cache_instructions: false`. Verify provider support with a request. Correct 12h cache-write pricing remains tracked in [issue #1387](https://github.com/converge-ai-labs/agent-foundation/issues/1387) pending upstream usage and pricing support.

**Response storage:** `openai.responses` defaults to `openai_store: false`, including existing models without that setting. Set `true` to opt in or `null` to delegate to the provider. This affects upstream response storage, not Service run history or other retention policies. Agent/run settings can override it; other calling APIs keep their own behavior. The ChatGPT subscription profile still requires storage off.

**Headers:** request-setting values are readable. Keep secrets in provider credentials or encrypted extra headers. Request settings cannot replace authentication, protocol, stored provider header names, or the affinity header, regardless of case. Model/provider changes take effect on later attempts, including resumed work, and settings are checked again before requests.

### Gateway session affinity

To keep a thread on a gateway's chosen backend:

1. Configure the gateway to route using a supported header.
2. Open the provider's advanced connection settings and set **Session affinity header** to that name (`config.session_affinity_header` through the API).
3. Continue a thread and check gateway logs for the same thread-derived UUID. Use a child or fork to check that it receives its own value.

Service supplies that UUID on every model path, including reviewers and media understanding. It derives the value from the thread, not the session or run. Clearing request header defaults does not remove it. Leave the affinity header empty to disable it; selecting a preset alone does not configure gateway routing.

### Model API availability

If saving an agent or validating settings returns `503 unavailable` with `{"dependency": "model_api:<api>"}`, the deployment no longer offers that calling API. Check the model's `config.model_api` against `GET /api/v1/provider-types/model`. Select an API the provider and deployment support, or ask the operator to restore it, then validate the configuration again.

## Media understanding

Give an agent a separate model for images, video, or audio its primary model cannot read. Configure workspace defaults for shared use, or select models in the agent's own `media_understanding` settings.

1. Add an enabled model with the matching capability, such as `image_understanding`. Its provider must also be enabled and usable in the workspace.
2. As a workspace administrator, open **Workspace settings → Media understanding** and select the model for that media kind.
3. Send a representative media input to an agent that needs this fallback. Check the answer and the selected model's usage record.

For the API, use `curl` and `jq`, the Service origin and workspace API key from above, and administrator permission. Set `IMAGE_MODEL_KEY` to your image model's key. Read the current defaults and their ETag before changing them:

```sh
curl -fsS -D media.headers -o media.json \
  "$A13N_URL/api/v1/media-understanding-defaults" \
  -H "Authorization: Bearer $A13N_API_KEY"
MEDIA_ETAG=$(awk 'tolower($1) == "etag:" {sub(/\r$/, "", $2); print $2}' media.headers)
jq --arg model "$IMAGE_MODEL_KEY" \
  '{image: $model, video: .video, audio: .audio}' media.json > media-update.json
```

Inspect `media-update.json`, then replace the defaults and read them back:

```sh
curl -fsS -X PUT "$A13N_URL/api/v1/media-understanding-defaults" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" -H "If-Match: $MEDIA_ETAG" \
  --data-binary @media-update.json
curl -fsS "$A13N_URL/api/v1/media-understanding-defaults" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

The response should show your image model and the unchanged video/audio selections. `PUT` replaces all three kinds: omission or `null` clears a kind. On `412`, read the current defaults again before deciding what to replace. Each selected model must declare the matching input capability.

At execution, an agent's own selection wins; if it is unusable, the run fails. An unusable workspace default is skipped with a warning, leaving that kind without a fallback model.

## Usage and prices

After a test run, inspect [usage](inspect-runs.md#usage) to see which model made each call. Service attributes and prices the call using the selected workspace model and saves the price snapshot with the usage record. This also applies to compaction, reviewers, media understanding, and child agents.

Two saved models can use the same upstream ID and still have separate prices. A provider's returned alias or dated model name does not change attribution. In a copied catalog price, `provider` and `model` record the price's source; the price still applies to the saved model that holds it. Update the saved `pricing` when your rates change rather than expecting a catalog refresh to do so.

## Model providers

<span id="chatgpt-subscription-provider"></span> <span id="self-hosted-callback"></span>

- [Model provider reference](model-providers.md)
