---
title: Models
description: Connect LLM providers in Service, register workspace models, configure reasoning and context defaults, set custom token pricing, and enable media analysis.
---

First [connect a model provider](model-providers.md#model-providers) with upstream credentials, then register workspace **models** that agents reference by immutable `key`.

## Add a model

In Console, open **Models → Add model**, pick a provider, select a catalog model (or enter an upstream ID manually), review capabilities/settings/pricing, and save.

Via API (`POST /api/v1/models`, requires `write` on the workspace and provider):

```json
{
  "provider_id": "mprov_...",
  "key": "local-llama",
  "name": "Llama (local)",
  "config": {
    "model_name": "llama3.3",
    "model_api": "ollama.chat_completions",
    "characteristics": {"context_window_tokens": 131072},
    "settings": {"max_tokens": 4096}
  }
}
```

- Query public metadata to prefill `characteristics`, `pricing`, and `catalog_ref` via `GET /api/v1/model-catalog?provider=openai&model=gpt-5.5`.
- Update a model with `PATCH /api/v1/models/{key}` and `If-Match` (`config` replaces the whole object; disable with `{"enabled": false}`).
- Declare native binary input support (`image_understanding`, `video_understanding`, `audio_understanding`, `document_understanding`) and optional `image_input` limits ([shared image policy](../a13n-harness/models.md#image-input-policy)) under `config.characteristics`.

### Advanced request settings

Set model-wide defaults in `config.settings` and agent-level overrides in `agent.config.model_settings` (for example, `openai_reasoning_effort`, `max_tokens`, `temperature`, `cache: "12h"`, `openai_store: false`, `extra_headers`, `extra_body`).

Precedence: Service baseline → Model `config.settings` → Agent `model_settings` (a per-run `model_settings` override replaces the agent's `model_settings` object on top of model defaults). For gateway prompt-cache routing, set `config.session_affinity_header` on the model provider.

## Media understanding

Configure fallback workspace models for images, audio, or video when the active agent's primary model cannot read that modality natively (**Workspace settings → Media understanding**, or `GET` + `PUT /api/v1/media-understanding-defaults` with `If-Match` and `{"image": "model-key", "video": null, "audio": null}`). An agent's explicit `media_understanding` field overrides workspace defaults.

## Usage and prices

Service attributes every call (including compaction, tool review, media understanding, and subagents) to the selected workspace model `key` and snapshots its saved `pricing` on the [usage record](inspect-runs.md#usage).

## Model providers

<span id="chatgpt-subscription-provider"></span> <span id="self-hosted-callback"></span>

- [Model provider reference](model-providers.md)
