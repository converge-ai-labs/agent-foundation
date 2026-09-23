# Models and authentication

A Model chooses a provider connection, request settings, and context budget. Store it in **`models/<name>.yaml` beside the selected root configuration**; Agents reference its `id`. Credentials are separate: Model authentication holds a reference, not the key or token.

## Choose your task

| Task                                          | Start here                                                                                    |
| --------------------------------------------- | --------------------------------------------------------------------------------------------- |
| Create a connection interactively             | `a13n-harness-ui add model`                                                                   |
| Write a complete API-key Model                | [Configuration recipe](configuration-recipes.md#change-the-model-reasoning-or-context-budget) |
| Point to a compatible endpoint                | [Custom endpoint recipe](configuration-recipes.md#connect-an-openai-compatible-endpoint)      |
| Find every Model field                        | [Model file reference](#model-file-reference)                                                 |
| Change request parameters                     | [Native request settings](#native-request-settings)                                           |
| Tune context or enable image input            | [Context and modality policy](#context-and-modality-policy)                                   |
| Use a subscription account                    | [Login](#subscription-login-and-api-keys), [Codex example](#codex-model-example)              |
| Change a Model only for this terminal session | [Temporary selection](#change-agents-during-a-conversation)                                   |

`settings` controls model requests. `model_configuration` controls connection wiring such as `base_url`. `model_characteristics` controls local context/input policy. These mappings are not interchangeable, and none belongs at the root of `a13n-harness-ui.yaml`.

## Create and manage Models

In the browser, open **Settings → Models** to add, edit or clone a saved Model. The list shows its route and owning source file. First-use setup uses the same editor, and an Agent's **Add model** action saves a new Model inline before selecting it. These operations do not send a model request or change an active Run.

1. Choose **Codex subscription**, **Grok subscription**, or an API connection. Subscriptions keep their native account stores and transports; they are not API-key presets.
2. Connect the shared subscription account, choose a saved API key, save a new key, or name a server environment variable. Credential writes happen independently; cancelling the Model draft does not undo a completed login or key save.
3. Search recommendations and the API directory, or enter a custom Model ID. Directory failure does not block manual IDs. Subscription choices are maintained separately. Directory context, release and modality metadata are descriptive, not an account-access test or permission to enable runtime capabilities.
4. Choose **Use this model**, then adjust reasoning, service speed and working context as offered for that connection. Changing an existing connection requires **Apply connection & defaults**; simply opening the editor preserves your settings. Presets change their owned settings and preserve unrelated native parameters.
5. Save the ordinary Model YAML. Advanced source editing retains access to `settings`, `model_configuration` and `model_characteristics`. Conversation selectors contain saved Models only, not every directory entry.

Native tools belong to **Agents**, not Models. The Model editor describes support; the Agent editor offers choices for its selected saved Model. Changing a Model does not silently replace an Agent's tools. Tools requiring provider stores, server URLs or other parameters remain configurable in advanced YAML. Provider usage charges may apply.

Terminal `setup`, `add model`, and the new-Model branch of `add agent` use the same backend choices and preparation rules. API connections can reuse saved-key metadata or an environment-variable reference without exposing key bytes. Subscription setup offers inline device/browser login or an explicit configure-later choice when an account is unavailable.

## Gateway session affinity

In **Add Model** or first-run setup, choose a **Gateway session affinity** preset or type a replacement in **Session affinity header**. CLI `add model` offers the same presets and custom entry. The saved result is an ordinary header name, not a preset reference:

```yaml
model_configuration:
  base_url: https://gateway.example.com/v1
  session_affinity_header: x-litellm-session-id
```

Set this inside the Model file, alongside `base_url`, **not** in `settings.extra_headers` or the root process configuration. Do not supply a session value: a stable UUID v5 derived from the current Thread ID is inserted automatically, using the [shared Harness derivation](../a13n-harness/models.md#automatic-model-request-affinity). The derived value is not saved in the recipe or Thread state. Leave the field absent or set it to `null` to disable it. To replace a preset, change only the name, for example to `x-company-session`.

| Preset                         | Header name            | Gateway prerequisite / boundary                                                |
| ------------------------------ | ---------------------- | ------------------------------------------------------------------------------ |
| LiteLLM                        | `x-litellm-session-id` | Enable session affinity on the gateway.                                        |
| Conversation ID                | `x-conversation-id`    | Configure a routing rule that reads this header first.                         |
| Bifrost (API-key affinity)     | `x-bf-session-id`      | API-key affinity only; not a guarantee of weighted provider or target pinning. |
| X-Session-ID (legacy / custom) | `x-session-id`         | Use when the gateway is explicitly configured to recognize it.                 |

Presets do not configure or detect the gateway. Sending the header requests affinity; it is not proof of a fixed target. Check the gateway's routing logs or target identifier if you need to verify routing. A connection test proves neither sticky routing nor cache reuse.

The value stays stable for the same Thread across turns and retries; independent Threads, child Threads, and forks use their own IDs. The name is captured in the immutable Run recipe. Editing the Model affects new compositions, not an already captured Run. This is separate from OpenAI prompt caching and Codex subscription protocol headers. Native xAI gRPC and subscription connections do not offer this gateway setting.

**Affinity value upgrade:** automatic gateway headers, prompt-cache keys, and bound Codex native session defaults now use a derived UUID rather than the raw Thread ID. Existing Threads may lose upstream cache or routing affinity once; their local IDs and history do not change and need no migration.

**Upgrade note:** older releases implicitly sent `x-session-id`. Omitted fields now disable gateway affinity, including in existing files and captures. Add `session_affinity_header: x-session-id` and start a new composition to retain that behavior. The legacy process-wide environment switch does not override Harness UI Model recipes. Configuration files are never automatically rewritten.

## Subscription login and API keys

```console
a13n-harness-ui auth status
a13n-harness-ui login codex
a13n-harness-ui login grok
a13n-harness-ui login codex --browser
a13n-harness-ui auth key list
a13n-harness-ui auth key set key-primary
a13n-harness-ui auth key delete key-primary
```

Device authorization is the default and needs no host callback. Open the printed URL yourself. Use `--browser` only when your browser can reach the host's loopback callback; Codex uses `http://localhost:1455/auth/callback`. There is no automatic fallback to another login method. Authorization expires within fifteen minutes. Replacing a different shared account requires `--allow-account-switch` through the CLI.

For API access, choose **API key** in initial setup, `a13n-harness-ui add model`, or the **Create a new model** branch of `a13n-harness-ui add agent`. Select the provider/protocol, confirm or edit its base URL, enter a key in the hidden credential field, choose a provider-specific model suggestion (or type a custom, case-sensitive model ID), select a settings preset, and review the working context budget. You can instead enter `key:key-primary` for a stored key or `env:OPENAI_API_KEY` for an environment variable available to the Harness UI process. A newly entered key is saved immediately under a fresh reference in the local key store, independently of configuration publication. Never paste an API key into the normal composer.

Stored API keys are plaintext in the data root's independent `auth.json`, with private permissions. Protect the host and backups. Configuration and Run snapshots hold references, not key bytes. A completed credential save/login is independent of setup publication and is not undone by cancelling setup.

## Native media input

Setup, `add model`, and `add agent` with a new connection automatically save known model input capabilities. The final summary shows the selected native media types; no separate capability question is required. Defaults come from a bundled, reviewed model catalog, not a network probe. They describe model capabilities, not account entitlement or continued model availability.

For example, a known image-capable OpenAI, Claude, or Grok Model includes:

```yaml
model_characteristics:
  capabilities: [image_understanding]
  context_window: 350000
  proactive_context_management_threshold: 0.65
  compact_threshold: 0.90
```

Known Gemini Models on the native Google API can also include `audio_understanding` and `video_understanding`. Defaults respect the selected protocol: an image-capable compatible route does not automatically gain native audio/video input just because the upstream model supports it through another API.

Capabilities are separate from an Agent's tools and from model output modalities. In particular, the file `view` tool uses these declarations to attach media directly to the active model. Without a matching capability, it requires an explicitly configured [media-understanding fallback](../a13n-harness/multimedia-understanding.md) or reports unavailability.

Unknown model IDs are labeled unknown and do not automatically enable media. Exact known IDs still receive starter defaults behind a custom base URL; verify that your endpoint supports the declared input. You can edit `model_characteristics.capabilities` in the Model file. Direct setup API callers can explicitly supply capabilities, including `[]`, to override defaults without changing their context policy.

Existing Model files are never backfilled automatically. If an older setup generated `capabilities: []` for an image-capable model, change only that field to `[image_understanding]` after checking the selected model and endpoint. Later Runs use the accepted configuration; active Runs and historical captures remain unchanged. Adding an Agent that reuses a Model preserves that Model exactly.

## Media understanding defaults

In **Settings → Models → Media understanding**, select a saved Model for Image, Video, or Audio and save. A Model's **Set as…** menu assigns or clears the same defaults. In the TUI, use `/model defaults` (also available from `/model`), choose a purpose and Model, then confirm **Save**. These are global settings, separate from the conversation's primary Model and the TUI's remembered Project Model.

```yaml
# Root configuration, including when selected with --config
media_understanding:
  image: model-vision
  video: model-video
  audio: null
```

Each reference must exist and declare the matching `image_understanding`, `video_understanding`, or `audio_understanding` capability. Unsupported Models are not selectable for that purpose. Remove references before deleting a Model or removing its required capability.

The file `view` tool remains native-first:

1. If the active Model declares the media capability, Harness attaches the media directly. No auxiliary Model or credentials are initialized.
2. Otherwise, the configured Model for that media kind describes or transcribes the file and returns text. Its own request settings, connection configuration, credential reference, and Thread affinity are used; the primary Model's settings and temporary `/thinking` or `/fast` overrides are not inherited.
3. An omitted or `null` reference uses the existing Harness environment fallback for that kind. **Environment** means a corresponding model environment variable is set; **Not configured** means none is set. Clearing a default does not disable the environment fallback. See [Harness multimedia configuration](../a13n-harness/multimedia-understanding.md) for variable names and settings.

A configured Model failure is reported, not silently replaced by an environment Model. Saving validates references and capability declarations without making a provider request or proving account access. Credentials are loaded only when auxiliary inference is needed. Provider charges may apply.

New Runs capture the selected Models' complete recipes, without secret bytes. Edits affect future captures, not already captured Runs or recovered continuations. Child Runs use the same configuration-generation and capture rules. This setting controls file `view` fallback; it does not convert unsupported composer attachments or force a proxy for native-capable Models.

## Starter model choices

Initial setup and new-Model creation offer these explicit subscription routes:

| Provider | Model                      | Best fit                                        |
| -------- | -------------------------- | ----------------------------------------------- |
| Codex    | `gpt-6-astra`              | Most demanding end-to-end reasoning and coding  |
| Codex    | `gpt-5.6-sol`              | Default; strong coding and reasoning            |
| Codex    | `gpt-5.6-terra`            | Everyday work with lower model cost             |
| Grok     | `grok-4.7`                 | Default; current coding and agentic model       |
| Grok     | `grok-4.5`                 | Previous generation with configurable reasoning |
| Grok     | `grok-4.20-0309-reasoning` | Earlier reasoning model with long context       |

Reviewed against the official [Codex model guide](https://developers.openai.com/codex/models), [xAI release notes](https://docs.x.ai/developers/release-notes), and [Grok 4.20 model page](https://docs.x.ai/developers/models/grok-4.20-beta-0309-reasoning) on September 7, 2026. The Grok 4.7 default was reviewed against the official [Grok 4.7 guide](https://docs.x.ai/developers/grok-4-7) on September 22, 2026. These choices do not query entitlement or promise that all subscription accounts can access every model. Grok choices are model generations, not three verified subscription price tiers. API-key setup offers provider-specific suggestions plus custom IDs. Lists expand to the terminal's available space and scroll with the focused choice. Suggestions are bundled starter choices, not live availability checks.

Auxiliary Models are named **Codex shell review** or **Grok shell review**. They are not selectable root Agents. Codex review uses Luna with low reasoning; Grok review uses 4.7 with low reasoning. Existing user-edited reviewer resources are preserved.

## API context defaults and readable names

API setup recommends **350,000 tokens**, or the bundled catalog's model context window when it is smaller. Unknown models also default to 350,000; the prompt labels this as a local working default, not a verified provider limit. Enter a positive token count such as `128000` or `128k` to override it. Set a lower budget if your endpoint or account requires one.

New API Models use the same native defaults as Codex: a summary reminder at **65%**, automatic compaction at **90%**, and the standard Harness summary prompts. At 350k the thresholds are **227,500** and **315,000** tokens. These settings are saved under `model_characteristics` and survive Run capture/reconstruction; reusing an existing Model does not change them. The catalog is bundled, so setup makes no model-discovery request.

Generated names identify the connection using ordinary text, for example **OpenAI - GPT-5.6 Sol**, **Z.AI - GLM 5.3**, or **Moonshot AI - Kimi K2.6**. Default Agent names add **- Coding**. Suggestions and saved names use the same rule, independently of terminal labels, colors, or status indicators. The add commands suggest non-colliding names and let you override them; custom Agent names do not erase their new Model's descriptive connection name. Configuration stays UTF-8 and custom names can contain Unicode. Existing resources are not renamed.

## Codex reasoning and context

The defaults are release-owned recommendations, not claims that every account supports every model or context size.

| Advanced setup choice | Working context budget | When to choose it                                                                             |
| --------------------- | ---------------------: | --------------------------------------------------------------------------------------------- |
| standard              |                272,000 | Conservative local budget matching the current Codex catalog default                          |
| balanced              |                350,000 | Default for repository work                                                                   |
| extended              |                872,000 | Large tasks where your account supports the catalog maximum; expect greater latency and usage |

A **working budget** controls local reminders and compaction. It does not increase the provider's limit or grant access. The default reminder threshold is 65% and automatic compaction starts at 90%, based on the latest reported root request footprint rather than cumulative tokens. At 350k these are 227,500 and 315,000 tokens.

Use `/thinking` to see the choices supported by the selected Model and installed adapter. The menu can offer effort levels, explicit token-budget presets, or Off; it does not offer a universal list. `/thinking default` returns to the selected Model's configured settings, including provider-native thinking fields. The status line describes the requested setting, not a measured provider result. High reasoning is independent of detailed display: you can use high reasoning while seeing concise output. Only provider-exposed reasoning is shown, and some providers do not return it.

Codex subscription requests do **not** receive an API output-token cap copied from YAACLI presets. The official Pydantic AI Codex profile strips unsupported generic settings such as `max_tokens`; `openai_store` is forced false. Explicit `openai_*` settings otherwise follow upstream validation rather than a separate Harness filter.

## Pro reasoning mode

On supported OpenAI Responses and Codex Models, `/pro` toggles the requested reasoning mode for subsequent Runs. `/pro on` selects Pro; `/pro off` selects Standard, **not** thinking off; `/pro reset` inherits the selected Model's configuration. If the Model is configured with Pro, resetting returns to Pro. An unset configuration is shown as Provider default, not Standard.

Mode is independent of `/thinking` effort, `/fast` processing, and `openai_reasoning_summary` (the provider-exposed summary preference). The override survives `/new` and in-process `/resume`, but changing Agent or Model clears it and a new TUI process does not restore it from history. Unsupported connections and conflicting `extra_body.reasoning` controls reject explicit choices rather than silently ignoring them. Availability comes from the installed SDK profile, not an account access check.

For a permanent default, edit the selected Model:

```yaml
settings:
  openai_reasoning_mode: pro  # or standard; remove for provider default
  thinking: high
  openai_reasoning_summary: detailed
```

The WebUI offers the same independent Reasoning mode control in Model settings, with the Model default always visible and a Use default action. The Model resource editor saves a permanent Standard/Pro choice; its Provider default choice removes the native field. Changing these settings never relabels an already captured Run. Pro access, usage, and latency depend on the provider; selecting Pro does not guarantee entitlement.

## Fast mode and service tiers

Codex onboarding adds a **Fast / Standard** choice after the model, defaulting to **Fast**. Fast saves `settings.openai_service_tier: priority`; Standard saves `settings.openai_service_tier: default`. The choice also appears when creating a new Codex Model with `add model` or `add agent`. Reusing a Model or loading an existing configuration does not change its tier.

### Temporary: use `/fast`

In an idle TUI, these commands affect subsequent Runs without rewriting YAML or saved Thread configuration:

| Command       | Requested setting                                       |
| ------------- | ------------------------------------------------------- |
| `/fast`       | Toggle effective priority on/off                        |
| `/fast on`    | `service_tier: priority`                                |
| `/fast off`   | `service_tier: default`                                 |
| `/fast reset` | Remove the override and inherit the Model configuration |

**Off is not reset:** if your Model is configured for priority, `/fast off` requests standard service, while `/fast reset` returns to Fast. The override lasts for this TUI process, including `/new` and `/resume`. Selecting a Model or Agent clears it; restarting does not restore a tier override from history. `/thinking` changes reasoning independently and preserves the tier.

This is a generic Model setting, not a Codex-only command. It applies to the root Model and Markdown children that inherit it, not explicitly configured child or auxiliary Models. **Fast** in the status bar and the requested service tier in `/status` describe the effective request, not proof that the provider fulfilled priority. Provider/model/account support varies; priority may consume more quota or cost more, and speed is not guaranteed. Unsupported settings retain the native integration's behavior; Harness UI does not silently retry at another tier.

### Permanent: edit the Model

Use `/config` to locate the selected configuration directory. For OpenAI/Codex, add or update this field in its `models/<name>.yaml`, preserving the other settings:

```yaml
settings:
  openai_service_tier: priority
```

Use `openai_service_tier` when writing OpenAI/Codex configuration. The generic `service_tier` remains supported as a compatible alias; if both fields are configured, `openai_service_tier` takes precedence. Keep only one field to avoid conflicting values. Other providers retain their native or generic service-tier settings.

Set it to `default` for permanent standard service, or remove the configured tier fields to leave the choice to the provider. Native integrations also accept generic `auto` and `flex` where supported; Fast specifically means `priority`, not a different model or reasoning level. Validate with `a13n-harness-ui config validate` (pass the same `--config` when using a custom configuration). Use `/fast reset` to remove an active temporary override; future Runs load the Model's current file settings.

## Codex Model example

Save this as `models/codex.yaml` beside the root configuration.

`models/codex.yaml`:

```yaml
schema_version: "1"
kind: model
id: model-codex
name: Codex - GPT-5.6 Sol
route: openai-codex:gpt-5.6-sol
authentication:
  kind: codex_subscription
settings:
  thinking: high
  openai_reasoning_summary: detailed
  openai_store: false
model_characteristics:
  context_window: 350000
  proactive_context_management_threshold: 0.65
  compact_threshold: 0.90
```

## Model file reference

Each file uses `schema_version: "1"`, `kind: model`, a unique `model-` `id`, and a human-readable `name`.

| Field                   | Default  | Meaning                                                                                        |
| ----------------------- | -------- | ---------------------------------------------------------------------------------------------- |
| `route`                 | Required | Supported provider/model route, such as `openai-responses:gpt-5` or `openai-codex:gpt-5.6-sol` |
| `authentication`        | Required | One explicit authentication form below                                                         |
| `settings`              | `{}`     | Native request settings passed through to Harness/Pydantic AI                                  |
| `model_configuration`   | `{}`     | Optional `base_url` for supported HTTP/API-key providers; empty for subscriptions              |
| `model_characteristics` | `null`   | Optional native Harness context/capability policy                                              |

Authentication accepts exactly one form:

```yaml
# Environment variable, read at native use:
authentication:
  kind: api_key
  env: OPENAI_API_KEY
```

```yaml
# Previously saved with `a13n-harness-ui auth key set key-primary`:
authentication:
  kind: api_key
  credential_ref: key-primary
```

```yaml
# Compatible account store, no literal token:
authentication:
  kind: codex_subscription
```

Grok uses `kind: grok_subscription` and a compatible `grok:` route. API-key authentication requires exactly one of `env` and `credential_ref`. A subscription kind must match its route. There is no silent fallback to another provider's credentials.

### Context and modality policy

Within `model_characteristics`:

| Field                                    | Default when the object is supplied | Meaning                                                                                     |
| ---------------------------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------- |
| `capabilities`                           | `[]`                                | Optional native policy: `image_understanding`, `video_understanding`, `audio_understanding` |
| `context_window_tokens`                  | `null`                              | Positive working context budget; omission retains native/catalog behavior                   |
| `proactive_context_management_threshold` | `0.65`                              | Fraction 0–1, or `null` to disable the derived proactive threshold                          |
| `compact_threshold`                      | `0.90`                              | Fraction greater than 0 and at most 1                                                       |

The legacy name `context_window` is still accepted in configuration and saved snapshots. New serialization uses `context_window_tokens`; if both are supplied, the canonical name takes precedence. Existing files do not need to be rewritten for this rename.

These values guide Harness behavior; they do not give a model modalities or token entitlement it lacks. Agent-level explicit context-capability thresholds remain authoritative. Review the selected provider's supported settings before changing a generic example.

### Account-store locations

Codex shares its supported file store under `CODEX_HOME` (default `~/.codex`). Harness UI respects the upstream credential-store policy and reports unsupported stores rather than replacing them. Grok uses `GROK_AUTH_PATH` before `GROK_HOME` or its default file; inline `GROK_AUTH` is not a shared writable-login mode. Account inspection does not log in or refresh credentials. Codex model requests use Pydantic AI 2.41 or later with an explicit shared-store credential source. A provider caches credentials within its lifetime and rereads storage before refresh, not on every request. A new Run or account operation gets a fresh provider. If refreshed credentials cannot be saved, the request fails, but the provider retains the rotated credentials in memory; resolve the store conflict and start a new Run rather than assuming the rotation was persisted. Grok retains its Harness-owned refresh lifecycle.

Harness retains device login, Thread affinity, routing hints, and per-run turn state where the official Codex provider has no equivalent. Browser PKCE and callback handling use the official flow; a small login-exchange adapter retains the real ID token required by native Codex `auth.json`. New login and account switching write that ID token, and same-account refresh preserves it. No second subscription store is created.

```console
a13n-harness-ui auth status codex --format json
a13n-harness-ui auth logout codex
a13n-harness-ui login codex --allow-account-switch
```

Logout and account replacement are explicit credential mutations; inspect which shared account/store you are changing. Never post account files, tokens, or stored-key files in diagnostics.

## Change agents during a conversation

`/agent` lists configured Agents with their model routes. `/agent agent-primary` switches the full Agent configuration for the next operation and resets session reasoning. `/model` instead lists configured Models; `/model <model-id>` overrides only the model and remembers the choice per Project, leaving the Agent unchanged. `/model default` clears both the override and the Project preference. It survives conversation navigation, Agent selection, and TUI restarts. An explicit launch `--agent` skips the preference; headless runs and API callers do not inherit it. See [everyday model selection](everyday-use.md#everyday-interaction) for recovery and reset behavior. History and Environment selection remain intact, and YAML is not rewritten. Add another model-backed Agent with `a13n-harness-ui add agent`, then switch to it.

`/thinking low` changes reasoning without editing files; `/thinking default` returns to the effective Model's configured settings. An in-flight operation keeps its captured values. An inherited Markdown child receives the parent's effective recipe; an independently referenced Agent keeps its own Model.

`/status` shows observed root usage and, for Codex, read-only subscription limit information. `/usage reset` separately opens explicitly confirmed credit redemption. Local observed cost is an estimate, not your subscription bill. See [usage and credit confirmation](everyday-use.md#tasks-usage-and-terminal-feedback).

## API providers and settings presets

The guided HTTP/API-key catalog maps to Pydantic AI integrations for OpenAI Responses, OpenAI-compatible Chat Completions, Anthropic, Google Gemini API, OpenRouter, DeepSeek, Z.AI / GLM, Moonshot AI / Kimi, Groq, Mistral, Together AI, and Fireworks AI. xAI has two separate API-key choices: `grok:` uses Chat Completions, while `xai:` uses the native SDK's default gRPC endpoint and exposes native X Search and other xAI tools. The native SDK choice skips the HTTP base-URL step. Both are separate from Grok subscription authentication. For other OpenAI-compatible services, select **OpenAI-compatible · Chat Completions** and provide that service's URL and model ID. Cloud IAM and subscription transports are not generic URL/key connections.

The preset picker shows the output limit alongside thinking, and the last Environment or name question shows the assembled connection and settings before saving. First-use landing, `add model`, and `add agent` with a new Model share these creation-time presets. Presets write normal editable YAML:

- **OpenAI Responses:** for models recognized by the upstream profile as reasoning-capable, high thinking, `openai_reasoning_summary: detailed`, and `openai_store: false`. Low, medium, extra-high (`xhigh`), and provider-default options are available. Unknown or non-reasoning models (such as GPT-4.1) default to neutral settings without reasoning-summary parameters. Chat Completions does not receive Responses-only summary fields.
- **Anthropic:** adaptive thinking with returned summaries and high effort for newer supported model profiles; otherwise interleaved extended thinking with an 8,192-token budget and a 16,384-token output cap. The interleaved preset explicitly enables `interleaved-thinking-2025-05-14`; adaptive thinking interleaves automatically. Models whose upstream profile rejects budget thinking only offer adaptive and provider-default presets.
- **Google Gemini:** native thinking presets request available thought summaries and map effort to the selected model's supported level or budget.
- **OpenRouter:** reasoning presets request returned reasoning with `exclude: false`.
- **DeepSeek, GLM, and Kimi:** dedicated native integrations retain returned `reasoning_content` and send it back during tool continuation and later turns. Recognized thinking models offer **Thinking · preserved** (`thinking: true`); GLM additionally saves `zai_clear_thinking: false` and uses native Z.AI request translation. Select `deepseek:`, `zai:`, or `moonshotai:` rather than a generic OpenAI-compatible route to retain these provider-specific behaviors. A generic “off” option is not offered: some models always think, and Kimi's native disable control is not equivalent to unified `thinking: false`.

Streaming parsing, tool-result continuation, and serialized next-turn replay are regression-tested against mocked native SDK HTTP responses for DeepSeek Reasoner/V4 Pro, GLM 4.7/5.3, and Kimi K2.5/K2 Thinking. These tests verify local request fidelity, not live account access or provider availability.

Choose **Provider defaults** when the model does not support the proposed reasoning settings. This option does not insert `max_tokens`; the native adapter/provider retains its default, which can still limit output. Presets do not establish entitlement or raise provider token limits. “Returned thinking” means the provider's exposed content or summaries, not private internal reasoning. Existing resource files are never migrated to new preset defaults.

### Paired output budgets

For the exact reviewed model IDs below, selecting a thinking preset also saves `settings.max_tokens`. These are **per-request output recommendations**, not total Run limits, model maximums, or targets that force the model to generate that much text. Reasoning can consume the output allowance according to the provider's native accounting. Working context controls reminders and compaction independently; choosing a smaller working context does not scale these values.

| Connection and reviewed model IDs                                                                                                 | Thinking choice              |       Saved `max_tokens` |
| --------------------------------------------------------------------------------------------------------------------------------- | ---------------------------- | -----------------------: |
| OpenAI Responses / Chat Completions: `gpt-5`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.5`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-6-astra` | low / medium / high or xhigh | 16,384 / 32,768 / 65,536 |
| Anthropic: `claude-sonnet-4-6`, `claude-opus-4-6`, `claude-haiku-4-5`, `claude-sonnet-4-5`                                        | adaptive / interleaved       |          32,768 / 16,384 |
| Google: `gemini-3.1-pro-preview`, `gemini-3.5-flash`, `gemini-2.5-pro`, `gemini-2.5-flash`                                        | low / medium or high         |          16,384 / 32,768 |
| DeepSeek: `deepseek-v4-pro`, `deepseek-v4-flash`, `deepseek-reasoner`                                                             | Thinking · preserved         |                   32,768 |
| Z.AI: `glm-5.3`, `glm-5.2`, `glm-4.7`, `glm-4.5`                                                                                  | Thinking · preserved         |                   32,768 |
| Moonshot AI: `kimi-k2.6`, `kimi-k2.5`, `kimi-k2-thinking`                                                                         | Thinking · preserved         |                   32,768 |
| OpenRouter: `openai/gpt-5.4`                                                                                                      | low / medium / high          | 16,384 / 32,768 / 65,536 |
| OpenRouter: `anthropic/claude-sonnet-4.6`, `google/gemini-2.5-pro`                                                                | low / medium or high         |          16,384 / 32,768 |

The selected native profile still determines which thinking presets are offered. For Gemini 2.5, native high thinking uses a 24,576-token thinking budget; its 32,768-token output cap leaves room for the answer. Claude's explicit 8,192-token interleaved thinking budget retains its existing 16,384-token cap, including for custom model IDs. Unreviewed Claude adaptive presets also retain the existing 16,384-token baseline. Other unreviewed models and routes get no new output cap; adding a suggestion or matching a model-name prefix does not opt a model into a budget. Exact reviewed IDs receive the same editable recommendations behind custom endpoints, whose limits may differ. Codex and Grok subscription creation is unchanged; no API preset is applied to those transports.

Review the saved value for your endpoint, context size, reasoning needs, latency, and cost. You can edit `settings.max_tokens` independently afterward. Changing `/thinking` does **not** recalculate it. Reusing a Model, loading an existing file, or passing explicit settings to the setup API does not apply a preset. There is no runtime auto-budget policy, context-overflow guarantee, Harness preset dependency, or network lookup during setup.

The release-owned recommendations were checked against provider references on September 10, 2026: [OpenAI model limits](https://developers.openai.com/api/docs/models/gpt-5.6-sol), [Claude model limits](https://platform.claude.com/docs/en/models/sonnet-4-6/overview), [Gemini output limits](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-pro), [DeepSeek model details](https://api-docs.deepseek.com/quick_start/pricing), [GLM model details](https://docs.z.ai/guides/llm/glm-5.3), [Moonshot's model card](https://huggingface.co/moonshotai/Kimi-K2.5), and [OpenRouter's model metadata](https://openrouter.ai/api/v1/models). These sources describe model capabilities and examples, not a promise that a custom endpoint or account will accept every setting. Native SDK request serialization is tested with mock HTTP; it is not a live provider test. In particular, the installed native DeepSeek adapter emits `max_completion_tokens`, whereas DeepSeek's API reference documents `max_tokens`; acceptance and enforcement of that alias have not been verified against the live service. Harness UI does not override the adapter's field mapping.

After initial setup, `add model` saves only a reusable Model, then offers a separate Add Agent flow for that Model. `add agent` first lets you select an existing Model or create a new one, then saves a new Agent. Reusing a Model references it directly without copying or modifying its settings. Both commands allocate separate identities for repeated names and leave existing Agents, Models, defaults, and conversations untouched. First-use landing creates the initial Model and Agent together without an existing-Model question. Setup and Add Agent initialize an absent root `security.shell_review` shortcut, preserving existing root selections. Subscription connections can use the separate reviewer Model; API-key setup reuses the connected Model. The shortcut applies across Agents with `risk_threshold: extra_high`, approval on flagged shell calls, and no added restriction on non-timeout review failure by default. Review timeout always denies execution. Add Model never modifies this root policy. Existing Agent files are not migrated. See [shell review configuration](configuration-recipes.md#configure-tool-review); the risk threshold is independent of the Model's thinking effort.

## Native request settings

For an API-key gateway using **Google Cloud**, author the Model route explicitly; the setup catalog's `google:` connection uses the **Gemini Developer API**, which is a different transport:

```yaml
route: google-cloud:your-gateway-model-id
authentication:
  kind: api_key
  credential_ref: key-gateway
model_configuration:
  base_url: https://gateway.example.com
```

Keep the gateway's model ID and endpoint. The native SDK adds its version and resource path; do not append `/v1beta` or `/v1beta1` merely to switch transports. Existing `google-gla:`, `google-vertex:`, and `gemini:` aliases use the same Cloud transport; `google-cloud:` makes that choice explicit. This API-key route does not configure Cloud IAM or service-account credentials. Validate the configuration, then test an actual request: acceptance alone does not prove gateway or model access.

`settings` is a JSON-compatible object passed through to Harness/Pydantic AI, not a Harness UI parameter allowlist. Native provider-specific and future options, nested objects, explicit `null`, and string whitespace are preserved in saved compositions and fresh Agent construction. The installed native Model and provider own parameter meaning, precedence, supported values, and errors at use time. Loading configuration or creating a Project does not validate a provider's request parameters or make a model request.

Use the documentation for your installed Pydantic AI/provider version. Examples include:

| Setting                                                                               | Purpose                                                                                    |
| ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `max_tokens`, `temperature`, `stop_sequences`                                         | Native generation controls; whitespace in stop sequences is significant                    |
| `thinking`, `openai_reasoning_effort`, `anthropic_thinking`, `google_thinking_config` | Generic or provider-specific reasoning; native precedence applies                          |
| `service_tier`, `openai_service_tier`                                                 | Generic or OpenAI service tier; a configured OpenAI-specific value takes native precedence |
| `extra_headers`, `extra_body`                                                         | Native request extensions, including nested JSON values                                    |
| `openai_prompt_cache_key`, `openai_store`                                             | OpenAI request options; Codex still applies its native subscription behavior               |

For example, an existing `settings.service_tier: priority` continues to work without renaming the field; newly written OpenAI/Codex configuration uses `settings.openai_service_tier: priority`. The terminal shows the configured native tier; an explicit `/fast on` or `/fast off` overrides the applicable native tier for subsequent Runs only, and `/fast reset` restores the file selection. The source file is not rewritten.

Opaque settings are retained verbatim, not secret-scrubbed by guessing field names. Use the dedicated authentication and MCP credential sources for secrets; do not place credentials in settings or extension configuration unless you intend those values to be persisted in local configuration captures. Diagnostics and settings display should be reviewed before sharing.

`model_configuration` is separate Host wiring, not request settings: it accepts an optional `base_url` for the HTTP/API-key providers offered by setup, the legacy `openai` alias, and the native `google-cloud` route and its aliases. Use an HTTP(S) URL without embedded credentials, query parameters, or fragments. Local HTTP endpoints are supported. Subscription endpoints cannot be overridden. The `xai:` native SDK route also requires empty `model_configuration`; it uses upstream's default gRPC endpoint and does not accept an HTTP `base_url`. Unknown Host constructor fields and unsupported routes still fail rather than being silently ignored.
