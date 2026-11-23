---
title: Models
description: Configure model selection, aliases, context budgets, image input policies, request affinity, and outbound HTTP proxy/TLS settings in Harness.
---

Configure which Model an Agent uses via `AgentSpec.model` (a string route such as `openai-responses:gpt-5`), or pass a concrete Pydantic AI `Model` instance to `HarnessBuilder().build(..., model=...)`.

## Model selection

- **Static route:** Set `AgentSpec(model="openai-responses:gpt-5", model_settings={"temperature": 0.1})`.
- **Dynamic resolver:** Supply a `model_resolver` on `RunBindings` when the Host resolves credentials, OAuth tokens, or per-tenant routes at Run time. See [Model authentication](model-authentication.md) for subscription OAuth flows.
- **Model characteristics:** Set `AgentSpec.model_characteristics` to declare `context_window_tokens`, compaction thresholds, and supported binary input modalities (`image_understanding`, `audio_understanding`, `video_understanding`, `document_understanding`).

## Image input policy

Control how Harness prepares images before sending them to the Model:

```python
from a13n_harness import AgentSpec, ImageInputPolicy, ModelCharacteristics

spec = AgentSpec(
    model_characteristics=ModelCharacteristics(
        image_input=ImageInputPolicy(
            support_gif=False,
            max_images=20,
            image_retention_ratio=0.6,
            max_image_bytes=5_242_880,  # 5 MiB base64 per image
        )
    )
)
```

Set `image_input=None` to disable automatic image preparation.

## Model request affinity

Harness derives a deterministic UUID v5 session affinity key from the active `thread_id` so providers that support prompt-cache routing (such as Codex and OpenAI-compatible gateways) can route consecutive turns in the same Thread to the same cache shard.

## Outbound HTTP proxies

Harness HTTP clients honor standard environment variables (`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, and lowercase equivalents) when creating provider transports.

## Outbound TLS verification

TLS verification is enabled by default. To trust a private enterprise CA, set `SSL_CERT_FILE` (or `REQUESTS_CA_BUNDLE`) in the Host environment or pass a configured `httpx.AsyncClient` to your provider.
