---
title: Models
description: Select a Model for each Run, set context policy, and configure gateway affinity and transport.
---

A Model connects an Agent to a provider or an offline test implementation. Choose where that Model comes from, then set its request options and local context policy. Harness can resolve the choice afresh for each Run while keeping the conversation in the same Thread.

## Choose a model source

| Need                                                   | Model source                                                         |
| ------------------------------------------------------ | -------------------------------------------------------------------- |
| A fixed provider route                                 | `AgentSpec(model="provider:model")`                                  |
| A native client, custom transport, or offline test     | `build(..., model=native_model)` with `AgentSpec.model` unset        |
| Current user, tenant, account, or route selection      | A string in `AgentSpec.model` and fresh `RunBindings.model_resolver` |
| Static custom gateway wiring shared by the Agent graph | Builder `gateway_provider_factory`                                   |

Start with the [offline quickstart](getting-started.md) environment. The next program needs no credentials or network. Provider examples later in this page require the selected integration's dependencies, credentials, and model access.

## Model selection

First, separate the saved conversation from current routing. Save this program as `models_demo.py` and run `uv run python models_demo.py`:

```python
import asyncio

from a13n_harness import AgentSpec, HarnessBuilder, HarnessState, RunBindings
from pydantic_ai.models.function import FunctionModel


async def main():
    resolutions = []

    async def respond(messages, info):
        yield "Route selected"

    async def resolve(context, model_id):
        assert model_id == "logical:support"
        resolutions.append((context.deps.run_id, context.deps.thread_id))
        return FunctionModel(stream_function=respond)

    executable = HarnessBuilder(configured_plugins_enabled=False).build(
        AgentSpec(model="logical:support"),
        output_type=str,
    )
    first = await executable.run(
        "First question",
        bindings=RunBindings.embedded(model_resolver=resolve),
    )
    print(first.output_or_raise())

    saved = first.state.model_dump_json()
    restored = HarnessState.model_validate_json(saved)
    second = await executable.run(
        "Follow-up question",
        previous_state=restored,
        bindings=RunBindings.embedded(model_resolver=resolve),
    )
    print(second.output_or_raise())
    assert first.run_id != second.run_id
    assert first.state.thread_id == second.state.thread_id
    assert resolutions == [
        (first.run_id, first.state.thread_id),
        (second.run_id, second.state.thread_id),
    ]


if __name__ == "__main__":
    asyncio.run(main())
```

It prints `Route selected` twice. The assertions show that the second Run has a new Run ID, retains the Thread ID, and invokes the resolver again after the State JSON round trip. The Model and resolver live in application code; the saved State carries conversation data.

To adapt this to your application:

1. Keep a logical selection such as `logical:support` in `AgentSpec.model`.
2. In `resolve(context, model_id)`, select an authorized account and route using current Host data.
3. Return a native Pydantic AI `Model`. Supply the resolver through fresh bindings on every Run, including continuation.

An async function or async callable object satisfies the resolver contract. Its context is `ModelResolutionContext[AgentContext]`; no Harness base class or registration is required. Internal recovery attempts share the current Run's resolver and context. A resolver may use a concurrency-safe Host client with a longer lifetime, provided the Host owns closing it.

For a concrete Model, omit the string selection and pass `model=`:

```python
from a13n_harness import AgentSpec, HarnessBuilder
from pydantic_ai.models.test import TestModel

executable = HarnessBuilder(configured_plugins_enabled=False).build(
    AgentSpec(system_prompt="Answer concisely."),
    output_type=str,
    model=TestModel(custom_output_text="Ready"),
)
```

This build uses the supplied Model directly and bypasses the Run resolver. For a fixed authenticated route, use `AgentSpec(model="openai-responses:gpt-5")` and omit `model=`. With no resolver, Harness constructs string selections through `infer_model()`.

Use `infer_model()` yourself when you need custom construction or synchronous Model patches:

```python
from a13n_harness import AgentSpec, HarnessBuilder, infer_model


def build_with_provider(provider_factory, apply_provider_profile):
    model = infer_model(
        "openai-responses:gpt-5",
        provider_factory=provider_factory,
        patches=(apply_provider_profile,),
    )
    return HarnessBuilder().build(AgentSpec(), output_type=str, model=model)
```

Here `provider_factory` is your function from provider name to native Provider; `apply_provider_profile` accepts and returns a native Model. Patches run in order. The helper also accepts static `common_headers`; request-specific `ModelSettings.extra_headers` win case-insensitively. It maps `openai:` to `openai-responses:` and legacy Google prefixes to `google-cloud:`. The result remains a native Model.

The literal `gateway@provider:model` selects Pydantic AI's public Gateway Provider, using `PYDANTIC_AI_GATEWAY_API_KEY` and optional `PYDANTIC_AI_GATEWAY_BASE_URL`. Other gateway names require your factory:

```python
from a13n_harness import AgentSpec, HarnessBuilder


def build_with_gateway(gateway_provider_factory):
    return HarnessBuilder(gateway_provider_factory=gateway_provider_factory).build(
        AgentSpec(model="company@openai:gpt-5"),
        output_type=str,
    )
```

The factory receives `(gateway_name, provider_name)` and returns a native Provider. It owns SDK configuration, credentials, retries, clients, and cleanup. The builder uses it for recursively built children too. Keep changing authorization or credentials in a Run resolver rather than a static gateway factory.

For explicit context facts, look up the package's immutable official catalog:

```python
from a13n_harness.model_catalog import get_official_model_catalog

models = get_official_model_catalog()
characteristics = models["anthropic:claude-sonnet-5"].characteristics
```

Each entry contains an official provider-qualified ID, `HarnessModelCharacteristics`, and a source URL. Assign the returned characteristics explicitly when building your spec. The builder does not apply catalog entries automatically. The catalog holds model facts; routes, credentials, request presets, reasoning choices, aliases, labels, and application defaults belong to the Host.

## Outbound HTTP proxies

Set proxy variables in the process that launches your Host. Routes built with `create_model_http_client()`, including Harness UI's shared API-key routes, use `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY`, including lowercase forms. For an installed Harness UI:

```bash
export http_proxy=http://127.0.0.1:8888
export https_proxy=http://127.0.0.1:8888
export no_proxy=localhost,127.0.0.1,::1
a13n-harness-ui
```

An HTTP proxy can carry HTTPS destinations through CONNECT. `httpx2` chooses the route and bypass matches; direct and proxy transports get the same bounded retry policy. An explicit `transport` owns routing. Setting `retry=None` disables retry while keeping environment proxy discovery.

With `httpx2` 2.14.0 or later, `NO_PROXY` supports IPv4/IPv6 CIDR and bracketed IPv6 with optional ports, such as `[::1]:8080`. For example, `NO_PROXY=localhost,127.0.0.0/8,10.0.0.0/8,[::1],fd00::/8,.internal.example` keeps matching destinations direct. CIDR matches IP literals in the URL; list hostnames and domain suffixes separately because bypass matching does not resolve DNS. Invalid entries are ignored while valid entries still apply.

Review existing CIDR rules when upgrading: an address that previously went through the proxy may now go direct. Provider endpoint validation and Run `allowed_hosts` still govern access. SDK transports outside this helper use their own proxy rules.

## Outbound TLS verification

Harness, Harness UI, and Service verify destination certificates and hostnames in the HTTP clients they construct. Keep this default in production and configure trusted CAs when needed. For controlled development with an interception proxy, an operator can explicitly disable verification before starting the Host:

```bash
export A13N_OUTBOUND_TLS_VERIFY=false
a13n-harness-ui
# Or start each Service control/worker process with the same environment.
```

Unset the variable or use `true` to enable verification. Values are case-insensitive and trimmed; only `true` and `false` are valid. Invalid values fail client construction and Service startup. Restart after changing the variable: clients retain their construction-time policy. This is a process setting, not a field in Model, Agent, Provider, Run, YAML, or TOML configuration.

The policy covers owned Model clients, including adapter-created Bedrock Converse, Web/media, remote MCP, owned OAuth exchanges, management, native HTTP Environment operations, and HTTP Envd attachments. It governs destination TLS on direct, proxy, and `NO_PROXY` routes.

Supplied clients, transports, and CA contexts keep their own policy. Dependency-created SDK clients, opaque inferred Model routes, database/object-store/telemetry SDKs, HTTPS proxy-hop TLS, and separate Envd daemon/broker processes also retain their own configuration.

> [!WARNING]
> `false` removes server authentication and permits interception of credentials and content. HTTPS remains encrypted, and application authentication, host restrictions, redirects, limits, and retries still apply.

## Model authoring aliases

Resolve short authoring choices before saving a definition. Use one resolver for Harness context characteristics and another for provider request settings:

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

The two results have different jobs:

| Alias                                                                               | Concrete effect                                                                                                 |
| ----------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `anthropic:context-200k`, `anthropic:context-400k`, `anthropic:context-1m`          | Harness context budget of `200_000`, `400_000`, or `1_000_000` tokens                                           |
| `anthropic:interleaved-thinking`                                                    | Anthropic adaptive thinking; leaves effort, token limits, cache, beta headers, and context management unchanged |
| `anthropic:thinking-disabled`                                                       | Disabled thinking; removes effort inherited from an earlier alias                                               |
| `anthropic:max-output-32k`, `anthropic:max-output-64k`, `anthropic:max-output-128k` | Request `max_tokens` of `32_768`, `65_536`, or `131_072`                                                        |

Context aliases control local lifecycle thresholds. Provider context variants, beta headers, and actual capacity remain provider choices. Max-output aliases set requested limits; the provider validates support for the selected Model.

Aliases apply in order, followed by concrete overrides. A selected alias requires a provider-qualified direct or gateway model string; unknown or incompatible aliases fail immediately. With no characteristics input, `resolve_model_characteristics()` returns `None`. Settings resolution returns a detached native dictionary. Hosts can supply immutable custom catalogs, then persist resolved values. Alias names stay out of `AgentSpec`, executable workers, and Harness State.

## Model characteristics

Put local input policy and context budgets in `model_characteristics`. An explicit context window also appears on the effective native `ModelProfile`, so native Capabilities see the same budget:

```python
from a13n_harness import AgentSpec, HarnessModelCharacteristics

spec = AgentSpec(
    model="logical:support",
    model_characteristics=HarnessModelCharacteristics(
        context_window_tokens=200_000,
        proactive_context_management_threshold=0.65,
        compact_threshold=0.90,
    ),
)
```

With these values, a selected `HandoffCapability()` derives its summarize reminder at 65%. A selected `CompactionCapability()` checks the 90% threshold before requests, using native `RunContext.model.context_window` and `context_window_used`, then Harness characteristics and captured usage when native values are unavailable. Explicit Capability token thresholds take precedence.

Select those Capabilities separately; setting ratios does not install them. Resolve characteristics from your own presets, the official catalog, or aliases. The builder does not guess them from a model name. Read them through `spec.model_characteristics`; Pydantic's class-level `model_config` has a different purpose. Native Pydantic AI `AgentSpec` remains usable without the Harness extension.

### Image input policy

Set `HarnessModelCharacteristics.image_input` to the frozen `ImageInputPolicy` exported by `a13n_harness`. It prepares input for the selected Model. Omission uses defaults; a partial policy fills unspecified members from defaults; explicit `null` disables automatic preparation. An explicit `ImageFilterCapability` keeps its own policy without an automatic duplicate. Parent Models, context budgets, and native request settings do not choose this policy.

| Parameter                | Default   | Meaning                                                                            |
| ------------------------ | --------- | ---------------------------------------------------------------------------------- |
| `support_gif`            | `true`    | Allow binary GIF input; `false` replaces it with explanatory text.                 |
| `max_images`             | `20`      | Maximum retained images across the request; `0` removes all images.                |
| `image_retention_ratio`  | `0.6`     | Fraction retained at first overflow, rounded up; greater than `0` and at most `1`. |
| `max_image_bytes`        | `5242880` | Base64-encoded bytes **per image**; `0` disables this byte limit.                  |
| `max_image_dimension`    | `8000`    | Maximum image axis in pixels; `0` disables this dimension limit.                   |
| `split_large_images`     | `true`    | Split tall static images into full-width segments before compression.              |
| `image_split_max_height` | `4096`    | Positive segment height in pixels.                                                 |
| `image_split_overlap`    | `50`      | Nonnegative overlap, strictly smaller than the segment height.                     |

For example, at the 21st image the default policy retains the newest 12, then lets the retained window grow to 20 before removing another batch. Replacement text for older images stays stable between batches. Choose `image_retention_ratio: 1` for a rolling newest-20 window. Counts apply after splitting; the default 20 is a request-size policy, not a provider limit.

The byte limit is 5 MiB of base64 data per image, separate from raw file size and total request size. Store exact bytes even if an editor displays MiB. GIF support and limits are explicit, never inferred from model names. Default-valued policies are omitted from serialization to preserve legacy canonical captures; explicit `null` remains. See [request-only image preparation](context.md#filters) for transformations and retained history.

## Model request affinity

To ask a gateway to keep requests from one Thread together, configure the header name it recognizes:

```python
from a13n_harness import HarnessBuilder

builder = HarnessBuilder(session_affinity_header="x-litellm-session-id")
```

Harness fills that header with a stable UUID v5 derived from `AgentContext.thread_id`. Omit the option or set `None` to disable gateway affinity. Only the selected header is sent; `x-session-id` is neither an extra automatic header nor a universal gateway standard. The gateway decides whether the header produces routing affinity.

Continuation keeps the value stable. Roots, children, siblings, and forks with distinct Thread IDs get distinct values. A trusted Host can choose IDs with `HarnessState.new(thread_id=...)` and `fork(thread_id=...)`. The derivation uses the exact ID and a fixed namespace, including when the ID already looks like a UUID. Its result is a 36-character lowercase hyphenated UUID.

Explicit native `extra_headers` win case-insensitively. Caller settings and internal IDs/events/telemetry remain unchanged. This wire identifier conveys affinity, not authentication.

For different gateways in one graph, leave the builder header disabled and wrap each resolved Model with `RequestHeadersModel` from `a13n_harness.models`. Set `common_headers={header_name: derive_model_affinity_id(context.deps.thread_id)}`, importing the derivation from `a13n_harness.model_affinity`. Use the current resolver context for each child. Harness UI stores this choice in `Model.model_configuration`; Service stores it in Provider `config`.

Prompt-cache keys are a separate default. Eligible Models receive `openai_prompt_cache_key=derive_model_affinity_id(thread_id)` unless an explicit setting already exists. Eligibility uses the final resolved `model_name`: `gpt-` immediately followed by an ASCII digit, optionally after exactly one `openai/`. Examples include `gpt-4.1`, `gpt-5-codex`, and `openai/gpt-5`; `gpt-oss-120b`, `o3`, DeepSeek, custom deployment names, other namespaces, different case, and leading whitespace do not qualify. The logical selection string does not decide eligibility.

Chat Completions and Responses adapters can transmit this as `prompt_cache_key`; compatible gateways still decide whether to accept it. Control the two defaults independently:

```python
from a13n_harness import HarnessBuilder

builder = HarnessBuilder(
    session_affinity_header="x-conversation-id",
    openai_prompt_cache_key_enabled=False,
)
```

An explicit cache-key boolean wins without reading the environment. Otherwise the default is `true`, with this process override:

```bash
export A13N_HARNESS_MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED=false
```

Accepted values are `1/true/yes/on` and `0/false/no/off`, case-insensitively, without surrounding whitespace. Invalid consulted values or non-boolean builder overrides fail construction. Each builder captures its header and cache policy once for the whole graph, including children. Disabling injection preserves explicit settings, including cache keys on non-GPT Models.

Codex native defaults are independent of these switches. In a resolver, construct `CodexRequestModel(..., thread_id=context.deps.thread_id)` with the raw ID. Its `session-id`, `thread-id`, and `x-client-request-id` use the same UUID derivation, independent of gateway headers; it can supply its own cache key even when Harness injection is off.

### Migrating existing connections

Automatic outbound values now use derived UUIDs instead of raw Thread IDs. Existing Threads switch once, possibly resetting upstream cache or routing affinity. Local history needs no migration. Explicit native values remain unchanged; embedded callers can provide a legacy value if their Host permits it.

Earlier implicit `x-session-id` behavior is removed. The old `x_session_id_enabled` argument is unavailable and its environment switch is ignored. To keep that header, explicitly set `session_affinity_header="x-session-id"` in the builder, `model_configuration.session_affinity_header` on each Harness UI Model, or `config.session_affinity_header` on a Service Provider.

Configuration files are not rewritten. Harness UI and Service ignore the legacy global switch so each connection owns its policy. Editing a Harness UI Model affects new Run captures, not existing captured compositions.

## Credentials and subscription authentication

Keep API keys, authenticated clients, and account selection in the Host. Native Providers supply API-key mechanisms; `a13n_harness.providers.model.oauth` supplies subscription building blocks for Codex, ChatGPT, Grok, and Copilot. Codex's Thread-aware adapter is `a13n_harness.models.codex.CodexRequestModel`.

Select authenticated Models again for a new Run. State, tool metadata, and `AgentSpec` are not credential stores. The Host owns user interaction, durable credentials, and permission to replace an account. Start with [authentication and HTTP clients](model-authentication.md), or use [Harness UI's login workflow](../a13n-harness-ui/models-and-authentication.md). The [authentication contract](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/a13n-harness/16a-model-authentication.md) describes the SDK/Host boundary.

## Thinking signature recovery

When a provider explicitly rejects a historical thinking signature, Harness can remove historical thinking and redacted-thinking parts and retry the current request once. A matching error includes `` Invalid `signature` in `thinking` block ``. The retry retains the same Model and settings, including thinking configuration. User input, assistant text, tool calls, completed results, and other content stay intact; tools do not run again.

Recovery requires both a matching rejection and removable content. A successful first request keeps history; a second failure propagates. Streaming recovery applies only before the adapter yields its stream. An opening SSE error can qualify after HTTP 200; an error after `message_start` establishes the stream cannot, even if no text has arrived.

The repair changes in-memory messages without rollback. Shared history carries the cleanup into later exports; detached request projections, such as image preparation, keep it local to the request. Existing saved snapshots stay unchanged until the Host saves new State. Disable automatic installation with `HarnessBuilder(self_healing_enabled=False)` if your application owns this behavior.

## Request settings versus context policy

Use `AgentSpec.model_settings` for native provider request options. Use `model_characteristics` for Harness context and input preparation. Set both to match the actual endpoint: local budgets cannot enlarge provider capacity, and capability declarations cannot add media support.

Continue with [context and working state](context.md), [inputs and outputs](inputs-and-outputs.md), or [usage and limits](usage-and-limits.md).

## Reusable provider definitions

To reuse typed connection construction across Hosts, implement `a13n_harness.providers.model.ModelProviderDefinition`. Its `build()` returns a native Model that Harness can accept directly. Built-ins live in `a13n_harness.providers.model.builtins`; compose and select them through `ProviderCatalog`.

The Host retains persistence, authorization, and current account selection. API-key credentials use `{"api_key": "..."}`; AWS and Google credentials retain structured fields. Use the OAuth module and Codex adapter described above for subscription integration.
