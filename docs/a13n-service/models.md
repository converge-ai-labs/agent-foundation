---
title: Models
description: Add model providers and models, configure media understanding, and track usage and prices.
---

An agent calls a **model**: an upstream model of a **model provider** account, with the model API to call it through, its capabilities and optional pricing. Providers and models belong to one [workspace](resources.md#workspaces), and agents select a model by its key.

## Model providers

In Console, open **Models → Add model → Connect a new provider**, or manage providers under **Workspace settings → Providers**. Through the API, create a provider in `/api/v1/model-providers` as described in [Providers](resources.md#providers).

![Console model provider catalog](../../.github/assets/console-model-providers.webp)

| Type                                                               | Model APIs (default first)                                                        |
| ------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| `openai`                                                           | `openai.responses`, `openai.chat_completions`                                     |
| `anthropic`                                                        | `anthropic.messages`                                                              |
| `google_gemini`, `google_vertex`                                   | `google.generate_content`                                                         |
| `azure_openai`                                                     | `openai.responses`, `openai.chat_completions`                                     |
| `aws_bedrock`                                                      | `bedrock.converse`, `bedrock_mantle.responses`, `bedrock_mantle.chat_completions` |
| `openrouter`                                                       | `openrouter.chat_completions`                                                     |
| `fireworks`, `together`, `cerebras`, `sambanova`, `vercel`, `xai`  | `openai.chat_completions`                                                         |
| `ollama`                                                           | `ollama.chat_completions`                                                         |
| `alibaba_model_studio`, `deepseek`, `moonshot`, `minimax`, `zhipu` | `openai.chat_completions`                                                         |
| `typesafe`                                                         | `typesafe.system_one`                                                             |

Each type's configuration and credential fields come from the Harness; `GET /api/v1/provider-types/model` returns them as JSON Schema. Most types take an optional `base_url` and an `api_key` credential. See [Harness models](../a13n-harness/models.md) and [model authentication](../a13n-harness/model-authentication.md) for each type's options.

Fireworks AI and Together AI support API-key credentials and optional `base_url` overrides. Fireworks defaults to `https://api.fireworks.ai/inference/v1`; Together defaults to `https://api.together.xyz/v1`. Use the full upstream model ID, for example `accounts/fireworks/models/llama-v3p3-70b-instruct` or `meta-llama/Llama-3.3-70B-Instruct-Turbo`. Their catalog channels are `fireworks-ai` and `togetherai`; availability depends on your account. Both offer a connection test without running inference.

A model provider may also carry up to 32 **extra request headers**, for gateways that route or bill by header. Header values are secrets: they are encrypted, never returned (views list only `header_names`), and edited per name in a `PATCH` (`"X-Team": "..."` sets a value, `null` removes it, omitted names are kept). Transport, authentication and protocol header names are refused.

Cerebras, SambaNova, Vercel AI Gateway, and xAI / Grok also accept API-key credentials and optional `base_url` overrides:

| Provider type | Default endpoint                  | Catalog channel |
| ------------- | --------------------------------- | --------------- |
| `cerebras`    | `https://api.cerebras.ai/v1`      | `cerebras`      |
| `sambanova`   | `https://api.sambanova.ai/v1`     | `sambanova`     |
| `vercel`      | `https://ai-gateway.vercel.sh/v1` | `vercel`        |
| `xai`         | `https://api.x.ai/v1`             | `xai`, `x-ai`   |

For Vercel, use the gateway's full model ID, such as `anthropic/claude-sonnet-4.6`. Verify its credentials by testing a saved Model: its public model list is not an authentication check. The other three offer a connection test without inference. The xAI / Grok provider uses HTTP Chat Completions. These provider choices do not expose Responses API or xAI's native server-side search tools.

## ChatGPT subscription provider

Create an `openai_chatgpt` provider; the default OSS flow needs no static credential. In **Workspace settings → Providers**, choose **ChatGPT subscription**, add it, then use **Sign in with ChatGPT**. This authorization belongs to the Provider and is shared by the workspace; it is not a personal Connection.

With the default OSS flow, after authorizing, copy the entire callback URL from the browser address bar into **Complete callback URL**, even if the loopback page shows a connection error. The Service can run remotely: it validates and exchanges the pasted callback without fetching that URL or requiring the browser to reach the server's loopback listener.

API clients use `POST /api/v1/model-providers/{id}/authorize` with `{}` and then `POST …/authorization/callback` with `{"attempt_id": "oauth_...", "callback_url": "http://127.0.0.1:1456/auth/callback?..."}`. Keep the full callback private. Invalid input is retryable; after exchange starts, a failed attempt must be restarted. `GET …/authorization` exposes credential-free status. `DELETE …/authorization` clears tokens before requesting revocation and reports when revocation is unconfirmed. The retained registration supports returning login; `{"new_registration": true}` starts a fresh account selection (a new OSS registration, or the same configured client without retained account hints).

Use `openai.responses` and an account model slug for this Provider. `GET …/models` returns account-visible slugs and display names; manual IDs remain available in Console. Visibility is not proof of inference entitlement. The endpoint is fixed, and tokens/pending authorization are encrypted in Provider-owned state rather than Model configuration or Run snapshots. Every request uses `store: false` and the required stream; native ordinary calls collect that stream. The native profile omits temperature, Top P and output-token limits; previous-response IDs and unsupported hosted tools fail explicitly. This does not translate the request into API-key behavior. Codex credentials are unrelated and cannot replace this authorization.

### Self-hosted callback

The default dynamic-registration flow stays on HTTP `127.0.0.1`, even after OpenAI issues a client ID; only the port can change. For an independently provisioned client, configure **OAuth client ID**, **Callback URL**, and **Token endpoint authentication** when creating or editing the ChatGPT Provider. Public clients use `none` with no secret; confidential clients use `client_secret_basic` and require **OAuth client secret**. Save changes before restarting sign-in. The Provider's `config.client_id` and `config.redirect_uri` override deployment defaults; blank fields inherit them. The secret is stored in the existing encrypted `credential.client_secret`, never in ordinary configuration or returned API data.

Deployment-wide public-client defaults can be configured before starting the Service:

```sh
export A13N_SERVER__PUBLIC_URL="https://agent.example.com"
export A13N_PROVIDERS__CHATGPT_CLIENT_ID="approved-public-client"
export A13N_PROVIDERS__CHATGPT_REDIRECT_URI="https://agent.example.com/api/v1/model-providers/oauth/callback"
```

These variables override `[providers] chatgpt_client_id` and `chatgpt_redirect_uri` in Service TOML. HTTPS callbacks must share the Service's configured public origin, so the initiating browser's cookie can return. Console reports automatic completion and refreshes status while the attempt is active; signing in must start from a user login session, not an API key. Manual callback paste remains available. To customize the public path, register that exact URI, set it on the Provider or in `CHATGPT_REDIRECT_URI`, and reverse-proxy map the path to Service's `GET /api/v1/model-providers/oauth/callback`, preserving the query and cookies. The callback never trusts Host/forwarded headers to choose its exchange URI. Exclude callback query strings from proxy/access logs.

Changing the client ID starts a new login without reusing another client's account binding; configuration or secret changes cancel pending/in-flight sign-ins; existing grants still refresh with their saved client authentication. **Use another ChatGPT account** keeps the configured client. Without a custom client, `CHATGPT_REDIRECT_URI` can override only the loopback port, not its scheme, host or `/auth/callback` path.

A website identity client is not permission to spend ChatGPT subscription quota. This integration still requires renewable tokens with direct-plan invocation scopes; hosted/commercial use needs separate OpenAI approval. Confidential clients send their secret only in the server-side HTTP Basic header, retaining PKCE; code exchange, refresh, and revocation use the authentication frozen with that grant. Issuer, protocol endpoints, resource, and required scopes are fixed by this integration, not user-configurable OAuth parameters.

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
    "settings": {"max_tokens": 4096}
  }
}
```

- `provider_id` names a model provider of the workspace, whose credential the model spends.
- `key` identifies the model in its workspace: in paths such as `/api/v1/models/local-llama`, in agent configurations and in usage. It defaults to the provider's type and the upstream model name joined by `-`, lowercased, with any other character a [key](resources.md#common-conventions) cannot hold replaced by `-` (`ollama-llama3.3` for an `ollama` provider here); set it when that is already taken (`409 already_exists`). It never changes.
- `config.model_name` is the upstream model name, and `config.model_api` must be one of the provider type's model APIs.
- `config.characteristics` declares the context window, context-management thresholds and `capabilities`: `image_understanding`, `video_understanding`, `audio_understanding`, and `document_understanding` for PDF documents.
- `config.settings` holds native request defaults, such as `thinking`, `max_tokens` or `openai_reasoning_summary`. An Agent's `model_settings` can override them. Existing flat `max_tokens`, `temperature`, `top_p`, `extra_body` and `extra_headers` remain supported; if also present in `settings`, the native entry wins by key, without a recursive merge.
- `pricing` prices the model's own calls in usage records; catalog items carry one to copy. The provider and model it names only record where the prices came from, so a catalog price copied for another endpoint's upstream model ID still applies.
- `catalog_ref`, such as `{"provider": "openai", "model": "gpt-5.5"}`, optionally records the catalog item's `ref` the model started from. Console uses it for the model's icon and name; the Service never checks it against the catalog.
- `enabled: false` creates the model disabled.

A model spends its provider's credential, so creating a model or changing its `config` needs `write` on the provider as well. The provider and model belong to the same workspace.

Change a model with `PATCH /api/v1/models/{key}` (`name`, `description`, `config`, `pricing`, `catalog_ref`, `enabled`) and its `If-Match`, `"{key}:{version}"`. Disable it with `{"enabled": false}`; models have no delete operation. Model-API-specific settings, such as reasoning effort, can be shared Model defaults (`config.settings`) or Agent revision overrides (`model_settings`). Both are validated against the provider type's `settings_schemas`. These schemas leave out operator timeouts, upstream model selection and provider-account conversation state (such as `openai_previous_response_id`, `bedrock_inference_profile`, `openrouter_models` or auxiliary `openai_moderation` models), and server-side tools such as `openai_native_tools`, which `max_usage` cannot fully account for.

### Image input preparation

In Model **Advanced**, **Image input** defaults to **Prepare images** on, **Support GIF** on, **Max images** 20 and **Max image size (MiB)** 5. Binary GIFs are removed from model requests when Support GIF is off; image URLs are not fetched or classified. Max images zero removes all images; size zero disables the byte limit. The size budget measures **base64-encoded bytes per image**; 1 MiB is 1,048,576 bytes, not a decimal MB or the original file size.

The API field is `config.characteristics.image_input`, not `config.settings`. Omission enables default preparation, an object customizes it, and explicit `null` disables automatic preparation. All seven fields are available through the API; see the [shared policy reference](../a13n-harness/models.md#image-input-policy). Console preserves unexposed dimension and splitting fields and exact byte budgets when you edit other controls.

Each primary, independent child and image-understanding Model owns its policy. An attempt uses its resolved Model configuration; later attempts, including recovery, resolve the current Model just like other live Model defaults. Unlike Harness UI, Service does not freeze a Model recipe across attempts.

### Advanced request settings

A Model's **Advanced** section offers Thinking effort and Max output tokens, plus Reasoning summary and Store response for OpenAI Responses. These fields edit the same draft as **Settings JSON**, where other native parameters remain available. Leaving a field at its default does not force a reasoning effort or output budget; choose values supported by your upstream model. Model defaults apply to agents using that Model, including reviewers and media understanding using their own selected Models.

For `openai.responses`, **Store response defaults to off**, even on existing Models that never set `openai_store`. Set `openai_store: true` to opt in, or `null` to delegate to the provider. This controls upstream response storage, not the Service's own run history or the provider's other retention policies. Agent and Run settings can override the default. Other calling APIs retain their existing storage behavior.

The API equivalent of a Model's Settings JSON is `config.settings`. On an Agent, use **Provider-specific settings** for `model_settings`. Both accept `extra_headers`; `openai.responses` and `openai.chat_completions` also accept `extra_body` for inference options the installed SDK does not yet know:

```json
{
  "extra_body": {"reasoning": {"effort": "future-effort"}},
  "extra_headers": {"x-experiment": "candidate"}
}
```

This Responses example illustrates passthrough, not an upstream-supported effort value. Chat Completions uses its own wire fields, such as `reasoning_effort`. Ordinary typed settings remain preferable when available. Raw values win over ordinary inference settings at the SDK's final shallow merge; a raw nested object replaces its generated counterpart rather than merging into it. Model selection, messages, tools, structured output and session state cannot be changed this way. Use native `openai_text_verbosity` instead of raw Responses `text`, whose container also carries structured output.

Omit either object to inherit its Model default; set it to `{}` to clear that default for the Agent or Run. A Run's settings replace the Agent's entire settings object, so `model_settings: {}` still inherits Model defaults. To clear both defaults, send `model_settings: {"extra_body": {}, "extra_headers": {}}`. `null` is not accepted for these objects. Model and Provider changes apply to later attempts, including resumed work; settings are checked again before requests.

Header values here are **readable configuration**. Put secrets in the Provider's credential or encrypted extra headers instead. Request settings cannot replace authentication, protocol, existing Provider header names or its session-affinity header, regardless of case.

### Gateway session affinity

Set **Session affinity** in the Provider's advanced connection settings to a header your gateway recognizes. The Service sends a stable Thread-derived UUID on every model path, including reviewers and media understanding. Continuing a Thread keeps the value; children and forks receive their own. It does not use the Service Session ID or Run ID, and clearing request header defaults does not disable it. The gateway must implement routing for that header; selecting a preset does not configure the gateway.

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
