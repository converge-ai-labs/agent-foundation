---
title: Models
description: Configure model routing, dynamic per-run model resolvers, context budgets, image input policies, and gateway session affinity in Harness.
---

Choose how Harness resolves a Model based on your application boundary:

| Need                                                   | Configuration                                                                |
| ------------------------------------------------------ | ---------------------------------------------------------------------------- |
| Fixed provider route                                   | `AgentSpec(model="openai-responses:gpt-5")`                                  |
| Concrete native client or offline `TestModel`          | `HarnessBuilder().build(..., model=native_model)` (`AgentSpec.model=None`)   |
| Per-Run tenant, account, or OAuth credential selection | Logical string in `AgentSpec.model` + fresh `RunBindings.model_resolver`     |
| Shared custom gateway factory across the Agent graph   | `HarnessBuilder(gateway_provider_factory=...)` with `gateway@provider:model` |

## Model selection

Keep portable `HarnessState` free of credentials by putting a logical ID in `AgentSpec.model` and resolving a native Pydantic AI `Model` per Run:

```python title="models_demo.py"
import asyncio
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from pydantic_ai.models.function import FunctionModel


async def respond(messages, info):
    yield "Route selected"


async def resolve(context, model_id):
    assert model_id == "logical:support"
    return FunctionModel(stream_function=respond)


async def main() -> None:
    executable = HarnessBuilder(configured_plugins_enabled=False).build(
        AgentSpec(model="logical:support"), output_type=str
    )
    result = await executable.run(
        "First question", bindings=RunBindings.embedded(model_resolver=resolve)
    )
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

Use `infer_model("openai-responses:gpt-5", provider_factory=..., patches=...)` when constructing or patching native Models synchronously, and `get_official_model_catalog()` from `a13n_harness.model_catalog` to look up official model context characteristics.

## Model characteristics and authoring aliases

Set `AgentSpec.model_characteristics` (`HarnessModelCharacteristics`) for local context budgets and `AgentSpec.model_settings` for provider request parameters, or resolve named presets via `resolve_model_characteristics` and `resolve_model_settings`:

```python
from a13n_harness import AgentSpec, HarnessModelCharacteristics
from a13n_harness.models import resolve_model_characteristics, resolve_model_settings
from pydantic_ai.settings import ModelSettings

model = "anthropic:claude-sonnet-5"
spec = AgentSpec(
    model=model,
    model_characteristics=resolve_model_characteristics(
        model,
        aliases=("anthropic:context-1m",),  # context-200k | context-400k | context-1m
        overrides=HarnessModelCharacteristics(compact_threshold=0.85),
    ),
    model_settings=resolve_model_settings(
        model,
        aliases=("anthropic:interleaved-thinking", "anthropic:max-output-128k"),
        overrides=ModelSettings(temperature=0.2),
    ),
)
```

### Image input policy

Configure `HarnessModelCharacteristics.image_input` (`ImageInputPolicy`, or `None` to disable request-time image preparation; see [Filters](context.md#filters)):

| Field                                                                  | Default               | Meaning                                                    |
| ---------------------------------------------------------------------- | --------------------- | ---------------------------------------------------------- |
| `support_gif`                                                          | `true`                | Allow binary GIFs (`false` replaces with explanatory text) |
| `max_images` / `image_retention_ratio`                                 | `20` / `0.6`          | Max images per request and fraction kept on overflow       |
| `max_image_bytes` / `max_image_dimension`                              | `5242880` / `8000`    | Per-image base64 byte cap (5 MiB) and max pixel axis       |
| `split_large_images` (`image_split_max_height`, `image_split_overlap`) | `true` (`4096`, `50`) | Split tall images before compression                       |

## Model request affinity

Configure gateway session headers and automatic `openai_prompt_cache_key` injection (enabled by default for `gpt-<digit>*` and `openai/gpt-<digit>*` models, or toggled via `A13N_HARNESS_MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED`):

```python
from a13n_harness import HarnessBuilder

builder = HarnessBuilder(
    session_affinity_header="x-litellm-session-id",
    openai_prompt_cache_key_enabled=True,
)
```

Harness populates both from a deterministic UUID v5 derived from `thread_id` (`derive_model_affinity_id` in `a13n_harness.model_affinity`).

## Outbound HTTP proxies

Routes constructed with `create_model_http_client()` honor `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY` (including CIDR blocks and domain suffixes).

## Outbound TLS verification

Owned HTTP clients verify certificates and hostnames by default. For local interception debugging only, set `A13N_OUTBOUND_TLS_VERIFY=false` in the Host process environment before startup.
