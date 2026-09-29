---
title: Models
description: Choose a model source, route per Run, and declare context characteristics and request affinity.
---

Use a native Pydantic AI Model or a model string. Harness adds per-run routing, explicit context characteristics, and Thread-based request affinity; provider adapters still own their wire protocols and supported settings.

## Choose a model source

| Situation                                               | Use                                                                       |
| ------------------------------------------------------- | ------------------------------------------------------------------------- |
| A fixed provider route                                  | `AgentSpec(model="provider:model")`                                       |
| A constructed client, custom transport, or offline test | `HarnessBuilder.build(..., model=native_model)` with no `AgentSpec.model` |
| User/tenant-specific routing or short-lived credentials | Fresh `RunBindings.model_resolver`                                        |
| Shared static gateway wiring                            | Builder `gateway_provider_factory`                                        |

These examples extend the [offline quickstart](getting-started.md). Names such as `provider_factory` and `route_store` denote application-owned collaborators, not built-in services.

## Model selection

Use exactly one model source. Put a native or Host-logical string in `AgentSpec.model`:

```python
executable = HarnessBuilder().build(
    AgentSpec(model="openai-responses:gpt-5"),
    output_type=str,
)
```

Pass a concrete Pydantic AI Model through `model=` and leave `AgentSpec.model` unset. You can construct it yourself or use the optional Harness helper:

```python
from a13n_harness import (
    HarnessBuilder,
    infer_model,
)

model = infer_model(
    "openai-responses:gpt-5",
    provider_factory=provider_factory,
    patches=(apply_provider_profile,),
)
executable = HarnessBuilder().build(
    AgentSpec(system_prompt="Answer concisely."),
    output_type=str,
    model=model,
)
```

`infer_model()` always returns a native Pydantic AI Model. It maps bare `openai:` to the modern `openai-responses:` provider, accepts legacy Google Cloud prefixes, applies synchronous Model patches in order, and can add caller-selected static common headers without overriding request-specific `ModelSettings.extra_headers`. You can bypass it and pass any native Model directly.

Without a run model resolver, `HarnessBuilder` uses this same helper for every string in `AgentSpec.model`. The literal `gateway@provider:model` form selects Pydantic AI's public Gateway Provider and its standard `PYDANTIC_AI_GATEWAY_API_KEY` and optional `PYDANTIC_AI_GATEWAY_BASE_URL` configuration:

```python
executable = HarnessBuilder().build(
    AgentSpec(model="gateway@openai:gpt-5"),
    output_type=str,
)
```

A named custom gateway uses one builder-level factory:

```python
executable = HarnessBuilder(
    gateway_provider_factory=gateway_provider_factory,
).build(
    AgentSpec(model="company@openai:gpt-5"),
    output_type=str,
)
```

The factory receives `(gateway_name, provider_name)` and returns a Pydantic AI `Provider`. It owns credentials, provider SDK configuration, retries, any HTTP client, and that client's lifecycle. The same builder factory applies to recursively built subagents. Use a fresh `RunBindings.model_resolver` instead when route authorization, credentials, or policy vary by run.

For direct-provider model facts, the package includes a small immutable official catalog:

```python
from a13n_harness.model_catalog import get_official_model_catalog

models = get_official_model_catalog()
characteristics = models["anthropic:claude-sonnet-5"].characteristics
```

Entries contain only a provider-qualified official model ID, objective `HarnessModelCharacteristics`, and an official source URL. They do not contain gateway routes, credentials, request presets, reasoning settings, aliases, labels, or application defaults. Lookup is explicit; `HarnessBuilder` does not silently apply catalog characteristics.

For run-specific routing, credentials, or tenant policy, pass an async function or async callable object through `RunBindings.model_resolver`. It receives the Pydantic `ModelResolutionContext` and string selection and returns a native Model. No Harness base class is required. A resolver can call Harness `infer_model()` with current Host-owned factories and patches, or return a self-constructed Model.

## Outbound HTTP proxies

Model routes using `create_model_http_client()`, including Harness UI's shared API-key routes, honor the standard `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY` environment variables and their lowercase forms. Set them in the process that launches the Host; no Model or UI configuration field is needed:

```bash
export http_proxy=http://127.0.0.1:8888
export https_proxy=http://127.0.0.1:8888
export no_proxy=localhost,127.0.0.1,::1
a13n-harness-ui
```

An HTTP proxy URL is also valid for HTTPS destinations: the client uses a CONNECT tunnel. Proxy selection and bypass matching follow `httpx2`; the same bounded HTTP retry policy applies to direct and proxied requests. An explicitly supplied `transport` keeps its own routing rather than adopting environment proxies, and `retry=None` disables retries without disabling proxy discovery.

Provider endpoint validation still applies, including local DNS checks where required. SDK-owned transports that do not use this helper retain their SDK's proxy behavior.

## Model authoring aliases

Use the two parallel resolvers when an authoring surface wants short, explicit names while keeping concrete values everywhere else. Context budgets resolve to Harness `HarnessModelCharacteristics`; provider request choices resolve independently to native `ModelSettings`:

```python
from a13n_harness import (
    AgentSpec,
    HarnessModelCharacteristics,
)
from a13n_harness.models import (
    resolve_model_characteristics,
    resolve_model_settings,
)
from pydantic_ai.settings import ModelSettings

model = "anthropic:claude-sonnet-5"
characteristics = resolve_model_characteristics(
    model,
    aliases=("anthropic:context-1m",),
    overrides=HarnessModelCharacteristics(compact_threshold=0.85),
)
settings = resolve_model_settings(
    model,
    aliases=(
        "anthropic:interleaved-thinking",
        "anthropic:max-output-128k",
    ),
    overrides=ModelSettings(temperature=0.2),
)

spec = AgentSpec(
    model=model,
    model_characteristics=characteristics,
    model_settings=settings,
)
```

The built-in characteristics aliases are:

- `anthropic:context-200k` for a `200_000`-token Harness context budget;
- `anthropic:context-400k` for a `400_000`-token Harness context budget;
- `anthropic:context-1m` for a `1_000_000`-token Harness context budget.

These values control Harness lifecycle thresholds only. They do not select a provider context variant, add beta headers, change request settings, or widen the model's actual context capability.

The built-in settings aliases are:

- `anthropic:interleaved-thinking`, which selects Anthropic adaptive thinking without forcing effort, token limits, cache behavior, beta headers, or context management;
- `anthropic:thinking-disabled`, which disables thinking and removes an effort inherited from an earlier alias;
- `anthropic:max-output-32k`, `anthropic:max-output-64k`, and `anthropic:max-output-128k`, which set `max_tokens` to `32_768`, `65_536`, and `131_072`, respectively.

A max-output alias is an explicit request limit, not a compatibility claim. The selected model and provider still validate whether the request is supported.

Within each resolver, aliases apply in declaration order and concrete overrides apply last. Alias resolution requires a provider-qualified direct or gateway model string and fails immediately for an unknown or incompatible alias. `resolve_model_characteristics()` returns `None` when neither aliases nor overrides provide characteristics; `resolve_model_settings()` returns an ordinary detached native settings dictionary. A Host can add private choices through immutable custom catalogs, but resolves every alias before persisting an Agent revision. `AgentSpec`, `HarnessBuilder`, workers, and Harness state never contain alias names.

## Model characteristics

The `model_characteristics` construction and serialization key holds resolved Harness-managed characteristics; it is not provider request settings or a second provider profile. Python code reads the value through `spec.model_characteristics` without conflicting with Pydantic's class-level `model_config`. It defines explicit Harness model capabilities together with the context window and proactive summarize and compaction ratios. An explicit Harness context window is also projected onto the effective native `ModelProfile`, so external Pydantic AI Capabilities can read the same value through the upstream model abstraction:

```python
spec = AgentSpec(
    model="logical:support",
    model_characteristics=HarnessModelCharacteristics(
        context_window_tokens=200_000,
        proactive_context_management_threshold=0.65,
        compact_threshold=0.90,
    ),
)
```

When selected, `HandoffCapability()` derives its summarize reminder at 65%. `CompactionCapability()` evaluates its 90% threshold at each request from native `RunContext.model.context_window` and `RunContext.context_window_used`, then falls back to Harness characteristics and captured provider usage when the native values are unavailable. An explicit Capability token threshold takes precedence. Model characteristics never enable either Capability by themselves. A Host may resolve these values from its own preset catalog, the Harness official model catalog, or characteristics aliases; `HarnessBuilder` never infers one from the model name. Native Pydantic AI `AgentSpec` remains accepted when this extension is not needed.

## Automatic model request affinity

Gateway affinity is **opt-in**. Configure the **header name**, not a fixed session value:

```python
builder = HarnessBuilder(session_affinity_header="x-litellm-session-id")
```

The Harness sends that header with a stable UUID v5 derived from the current `AgentContext.thread_id`, not the raw Thread ID. Omit the option (or use `None`) to leave gateway affinity disabled. Choose the name your gateway is configured to recognize; `x-session-id` is not a universal standard. A custom name replaces the legacy header rather than sending both. Sending a header requests affinity but does not guarantee provider pinning.

The value remains stable across continuation from the same `HarnessState` and differs for independent roots, children, siblings, and forks. A trusted Host can select the source Thread ID through `HarnessState.new(thread_id=...)`, while `HarnessState.fork(thread_id=...)` creates a distinct Host-selected branch. The Harness does not use transient Run IDs or mutate caller settings. In the embedded SDK, explicit native `extra_headers` still take precedence case-insensitively.

For different connections within one Agent graph, import `derive_model_affinity_id` from `a13n_harness.model_affinity` and bind each Model in your `RunModelResolver` using `RequestHeadersModel(model, common_headers={header_name: derive_model_affinity_id(context.deps.thread_id)})`; leave the Builder option disabled. Always use the current resolution context, not a captured parent's ID. Harness UI owns this in `Model.model_configuration`; Service owns it in live `ModelProvider.configuration`.

OpenAI prompt-cache keys are independently controlled: eligible models receive `openai_prompt_cache_key=derive_model_affinity_id(thread_id)` by default, using the same derived value as gateway affinity. Explicit cache settings take precedence and are not transformed.

The shared derivation uses a fixed namespace and the exact Thread ID, even when it is already a UUID. It produces a 36-character lowercase hyphenated UUID, with no persisted mapping, new state field, or dependency on the Run, Model, clock, or machine. Internal IDs, events, and telemetry remain unchanged. This is a bounded wire format, not an authentication token or a guarantee that every gateway accepts it.

Cache-key eligibility uses the final resolved Model's `model_name`, not a Host-logical alias. Names must begin with `gpt-` followed immediately by an ASCII digit, optionally prefixed by exactly one `openai/`. Thus `gpt-4.1`, `gpt-5-codex`, and `openai/gpt-5` qualify; DeepSeek, `gpt-oss-120b`, `o3`, and custom deployment names do not. Matching is case-sensitive and does not trim whitespace or strip arbitrary namespaces. Both Chat Completions and Responses adapters can transmit the setting as `prompt_cache_key`. This naming policy is not a guarantee that a compatible gateway accepts the field.

Hosts can independently control the defaults when constructing the builder:

```python
builder = HarnessBuilder(
    session_affinity_header="x-conversation-id",
    openai_prompt_cache_key_enabled=False,
)
```

The old `x_session_id_enabled` Builder option is removed and its environment switch is ignored. Use `session_affinity_header="x-session-id"` explicitly instead. The independent cache-key switch still defaults to **true**; an explicit `openai_prompt_cache_key_enabled` boolean overrides its environment value without reading it:

```bash
export A13N_HARNESS_MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED=false
```

Environment values accept `1/true/yes/on` or `0/false/no/off`, case-insensitively and without surrounding whitespace. Invalid consulted environment values or non-boolean Builder overrides fail construction. Each builder snapshots its header name and cache policy once for the entire executable graph, including children, so changing the environment does not alter existing builders or executables. Enabling the cache-key switch enables the GPT naming rule; it does not force injection for other models. A disabled patch leaves any explicit setting untouched, including a cache key on a non-GPT model.

These switches control only Harness defaults, not provider-native behavior. Bind `CodexRequestModel(..., thread_id=context.deps.thread_id)` explicitly in a resolver: its native `session-id`, `thread-id`, and `x-client-request-id` use the same UUID derivation independently of a gateway header. Pass the raw Thread ID to the adapter, not an already-derived UUID. The Codex adapter can still supply its own cache key when Harness injection is disabled.

### Migrating existing connections

Automatic gateway headers, prompt-cache keys, and bound Codex session defaults now use the derived UUID rather than the raw Thread ID. Existing Threads switch outbound values once, which may reset upstream cache or routing affinity. Local history is unchanged and no state migration is needed. Explicit native values remain unchanged; embedded callers needing the previous wire value can supply it explicitly, subject to their Host's validation policy.

Earlier versions implicitly sent `x-session-id` to every model. This default is removed, including for old configuration files that omit the new field. To preserve it, select `session_affinity_header="x-session-id"` in the embedded Builder, add `model_configuration.session_affinity_header: x-session-id` to each Harness UI Model, or set `configuration.session_affinity_header` on the Service Provider. No files are rewritten automatically. Harness UI and Service deliberately ignore the legacy global header switch so one connection's policy cannot leak into another. Existing captured Harness UI Run recipes are not rewritten; start a new Run composition after editing the Model.

## Credentials and subscription authentication

API-key Models use their native provider's credential mechanism. Keep secrets and client lifetimes in application code, not `HarnessState`, metadata, or an Agent preset. A model string alone is not proof of access.

The `a13n_harness.providers.model.oauth` module supplies SDK-level subscription building blocks: Codex browser/device login flows and `CodexRequestModel`; Grok credential-source and OAuth/device-flow types plus `build_grok_model`. The Host owns user interaction, account storage, persistence, and permission to replace accounts. Reconstruct authenticated Models for new Runs instead of treating an exported continuation as a saved client.

Use [Harness UI authentication](../a13n-harness-ui/models-and-authentication.md) for the ready-to-use local login experience. SDK integrations can start with [authentication and HTTP-client recipes](model-authentication.md), then follow the [Model authentication contract](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/a13n-harness/16a-model-authentication.md) and the public types in `a13n_harness.providers.model.oauth`; Harness does not supply a product account database.

## Request settings versus context policy

`AgentSpec.model_settings` contains native provider request settings. `model_characteristics` describes explicit local context and input policy. A larger local budget does not increase a provider limit, and an image declaration does not make an endpoint accept images.

Continue with [context and working state](context.md), [inputs and outputs](inputs-and-outputs.md), or [usage and limits](usage-and-limits.md).

## Reusable provider definitions

Use `a13n_harness.providers.model.ModelProviderDefinition` to contribute a typed connection and native SDK constructor. The result of `build()` is a native Pydantic AI Model; it can be passed directly to the Harness. Built-ins are available from `a13n_harness.providers.model.builtins`. Optional Model OAuth flows live in `a13n_harness.providers.model.oauth`, while the Thread-aware Codex adapter is `a13n_harness.models.codex.CodexRequestModel`.

A host composes Model definitions in code and selects them through a `ProviderCatalog`. The host owns persistence, authorization, and current account selection. API-key credential objects use `{"api_key": "..."}`; AWS and Google credentials retain their structured fields.
