# Models and authentication

Connect credentials separately from Model configuration. A Model file is reusable by multiple Agents; it does not contain credential bytes.

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

## Starter model choices

Initial setup and new-Model creation offer these explicit subscription routes:

| Provider | Model                      | Best fit                                        |
| -------- | -------------------------- | ----------------------------------------------- |
| Codex    | `gpt-6-astra`              | Most demanding end-to-end reasoning and coding  |
| Codex    | `gpt-5.6-sol`              | Default; strong coding and reasoning            |
| Codex    | `gpt-5.6-terra`            | Everyday work with lower model cost             |
| Grok     | `grok-4.6`                 | Default; current coding and agentic model       |
| Grok     | `grok-4.5`                 | Previous generation with configurable reasoning |
| Grok     | `grok-4.20-0309-reasoning` | Earlier reasoning model with long context       |

Reviewed against the official [Codex model guide](https://developers.openai.com/codex/models), [xAI release notes](https://docs.x.ai/developers/release-notes), and [Grok 4.20 model page](https://docs.x.ai/developers/models/grok-4.20-beta-0309-reasoning) on September 7, 2026. These choices do not query entitlement or promise that all subscription accounts can access every model. Grok choices are model generations, not three verified subscription price tiers. API-key setup offers provider-specific suggestions plus custom IDs. Lists expand to the terminal's available space and scroll with the focused choice. Suggestions are bundled starter choices, not live availability checks.

Auxiliary Models are named **Codex shell review** or **Grok shell review**. They are not selectable root Agents. Codex review uses Luna with low reasoning; Grok review uses 4.6 with low reasoning. Existing user-edited reviewer resources are preserved.

## API context defaults and readable names

API setup recommends **350,000 tokens**, or the bundled catalog's model context window when it is smaller. Unknown models also default to 350,000; the prompt labels this as a local working default, not a verified provider limit. Enter a positive token count such as `128000` or `128k` to override it. Set a lower budget if your endpoint or account requires one.

New API Models use the same native defaults as Codex: a summary reminder at **65%**, automatic compaction at **90%**, and the standard Harness summary prompts. At 350k the thresholds are **227,500** and **315,000** tokens. These settings are saved under `model_characteristics` and survive Run capture/reconstruction; reusing an existing Model does not change them. The catalog is bundled, so setup makes no model-discovery request.

Generated names identify the connection, for example **OpenAI · GPT-5.6 Sol**, **Z.AI · GLM 5.3**, or **Moonshot AI · Kimi K2.6**. Default Agent names add **· Coding**. The add commands suggest readable, non-colliding names and let you override them; custom Agent names do not erase their new Model's descriptive connection name. Existing resources are not renamed.

## Codex reasoning and context

The defaults are release-owned recommendations, not claims that every account supports every model or context size.

| Advanced setup choice | Working context budget | When to choose it                                                                             |
| --------------------- | ---------------------: | --------------------------------------------------------------------------------------------- |
| standard              |                272,000 | Conservative local budget matching the current Codex catalog default                          |
| balanced              |                350,000 | Default for repository work                                                                   |
| extended              |                872,000 | Large tasks where your account supports the catalog maximum; expect greater latency and usage |

A **working budget** controls local reminders and compaction. It does not increase the provider's limit or grant access. The default reminder threshold is 65% and automatic compaction starts at 90%, based on the latest reported root request footprint rather than cumulative tokens. At 350k these are 227,500 and 315,000 tokens.

Reasoning choices are `low`, `medium`, `high`, and `xhigh`. `/thinking default` returns to the selected Model's configured value. High reasoning is independent of detailed display: you can use high reasoning while seeing concise output. Only provider-exposed reasoning is shown, and some providers do not return it.

Codex subscription requests do **not** receive an API output-token cap copied from YAACLI presets. The native subscription adapter strips unsupported settings such as `max_tokens`; `openai_store` is forced false.

## Fast mode and service tiers

Codex onboarding adds a **Fast / Standard** choice after the model, defaulting to **Fast**. Fast saves `settings.service_tier: priority`; Standard saves `settings.service_tier: default`. The choice also appears when creating a new Codex Model with `add model` or `add agent`. Reusing a Model or loading an existing configuration does not change its tier.

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

Use `/config` to locate the selected configuration directory. Add or update this field in its `models/<name>.yaml`, preserving the other settings:

```yaml
settings:
  service_tier: priority
```

Set it to `default` for permanent standard service, or remove it to leave the choice to the provider. Native integrations also accept generic `auto` and `flex` where supported; Fast specifically means `priority`, not a different model or reasoning level. Validate with `a13n-harness-ui config validate` (pass the same `--config` when using a custom configuration). Use `/fast reset` to remove an active temporary override; future Runs load the Model's current file settings.

## Codex Model example

Save this as `models/codex.yaml` beside the root configuration.

`models/codex.yaml`:

```yaml
schema_version: "1"
kind: model
id: model-codex
name: Codex · GPT-5.6 Sol
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
| `settings`              | `{}`     | Provider request settings, validated by the route's adapter                                    |
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
| `context_window`                         | `null`                              | Positive working context budget; omission retains native/catalog behavior                   |
| `proactive_context_management_threshold` | `0.65`                              | Fraction 0–1, or `null` to disable the derived proactive threshold                          |
| `compact_threshold`                      | `0.90`                              | Fraction greater than 0 and at most 1                                                       |

These values guide Harness behavior; they do not give a model modalities or token entitlement it lacks. Agent-level explicit context-capability thresholds remain authoritative. Review the selected provider's supported settings before changing a generic example.

### Account-store locations

Codex shares its supported file store under `CODEX_HOME` (default `~/.codex`). Harness UI respects the upstream credential-store policy and reports unsupported stores rather than replacing them. Grok uses `GROK_AUTH_PATH` before `GROK_HOME` or its default file; inline `GROK_AUTH` is not a shared writable-login mode. Account inspection does not log in or refresh credentials. Native use refreshes supported expiring credentials through the shared account integration.

```console
a13n-harness-ui auth status codex --format json
a13n-harness-ui auth logout codex
a13n-harness-ui login codex --allow-account-switch
```

Logout and account replacement are explicit credential mutations; inspect which shared account/store you are changing. Never post account files, tokens, or stored-key files in diagnostics.

## Change agents during a conversation

`/agent` lists configured Agents with their model routes. `/agent agent-primary` switches the full Agent configuration for the next operation and resets session reasoning. `/model` instead lists configured Models; `/model <model-id>` temporarily overrides only the model for this TUI session, leaving the Agent unchanged. `/model default` clears that override. It survives conversation navigation and Agent selection within this TUI lifetime, but not a TUI restart. History and Environment selection remain intact, and YAML is not rewritten. Add another model-backed Agent with `a13n-harness-ui add agent`, then switch to it.

`/thinking low` changes reasoning without editing files; `/thinking default` returns to the effective Model's configured settings. An in-flight operation keeps its captured values. An inherited Markdown child receives the parent's effective recipe; an independently referenced Agent keeps its own Model.

`/status` shows observed root usage and, for Codex, read-only subscription limit information. `/usage reset` separately opens explicitly confirmed credit redemption. Local observed cost is an estimate, not your subscription bill. See [usage and credit confirmation](everyday-use.md#tasks-usage-and-terminal-feedback).

## API providers and settings presets

The guided HTTP/API-key catalog maps to Pydantic AI integrations for OpenAI Responses, OpenAI-compatible Chat Completions, Anthropic, Google Gemini API, OpenRouter, DeepSeek, Z.AI / GLM, Moonshot AI / Kimi, Groq, Mistral, Together AI, and Fireworks AI. xAI's Grok API uses its Chat Completions endpoint; it is separate from Grok subscription authentication. For other OpenAI-compatible services, select **OpenAI-compatible · Chat Completions** and provide that service's URL and model ID. Cloud IAM and subscription transports are not generic URL/key connections.

The last Environment or Agent-name question shows the assembled connection and settings before saving. Presets write normal editable YAML:

- **OpenAI Responses:** for models recognized by the upstream profile as reasoning-capable, high thinking, `openai_reasoning_summary: detailed`, and `openai_store: false`. Low, medium, extra-high (`xhigh`), and provider-default options are available. Unknown or non-reasoning models (such as GPT-4.1) default to neutral settings without reasoning-summary parameters. Chat Completions does not receive Responses-only summary fields.
- **Anthropic:** adaptive thinking with returned summaries and high effort for newer supported model profiles; otherwise interleaved extended thinking with an 8,192-token budget and a 16,384-token output cap. The interleaved preset explicitly enables `interleaved-thinking-2025-05-14`; adaptive thinking interleaves automatically. Models whose upstream profile rejects budget thinking only offer adaptive and provider-default presets.
- **Google Gemini:** native thinking presets request available thought summaries and map effort to the selected model's supported level or budget.
- **OpenRouter:** reasoning presets request returned reasoning with `exclude: false`.
- **DeepSeek, GLM, and Kimi:** dedicated native integrations retain returned `reasoning_content` and send it back during tool continuation and later turns. Recognized thinking models offer **Thinking · preserved** (`thinking: true`); GLM additionally saves `zai_clear_thinking: false` and uses native Z.AI request translation. Select `deepseek:`, `zai:`, or `moonshotai:` rather than a generic OpenAI-compatible route to retain these provider-specific behaviors. A generic “off” option is not offered: some models always think, and Kimi's native disable control is not equivalent to unified `thinking: false`.

Streaming parsing, tool-result continuation, and serialized next-turn replay are regression-tested against mocked native SDK HTTP responses for DeepSeek Reasoner/V4 Pro, GLM 4.7/5.3, and Kimi K2.5/K2 Thinking. These tests verify local request fidelity, not live account access or provider availability.

Choose **Provider defaults** when the model does not support the proposed reasoning settings. Presets do not establish entitlement or raise provider token limits. “Returned thinking” means the provider's exposed content or summaries, not private internal reasoning. Existing resource files are never migrated to new preset defaults.

After initial setup, `add model` saves only a reusable Model. `add agent` first lets you select an existing Model or create a new one, then saves a new Agent. Reusing a Model references it directly without copying or modifying its settings. Both commands allocate separate identities for repeated names and leave existing Agents, Models, defaults, and conversations untouched. First-use landing creates the initial Model and Agent together without an existing-Model question. New subscription Agents enable shell review with `risk_threshold: extra_high`; review errors still require approval. This risk threshold is independent of the review Model's low thinking effort.

## Supported request settings

All entries in `settings` are optional; omitted or `null` values leave provider/native defaults. This is the complete Harness UI adapter surface, not an unrestricted pass-through of every upstream provider option. A syntactically accepted option may still be unsupported by a particular model or subscription.

| Setting                                 | Accepted value                                                                                                                                                                       |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `max_tokens`                            | Integer at least 1; Codex subscription removes unsupported output caps                                                                                                               |
| `temperature`                           | Finite number                                                                                                                                                                        |
| `top_p`                                 | Number from 0 to 1                                                                                                                                                                   |
| `top_k`                                 | Integer at least 1                                                                                                                                                                   |
| `timeout`                               | Positive finite seconds                                                                                                                                                              |
| `parallel_tool_calls`                   | Boolean                                                                                                                                                                              |
| `tool_choice`                           | `none`, `required`, `auto`                                                                                                                                                           |
| `seed`                                  | Integer                                                                                                                                                                              |
| `presence_penalty`, `frequency_penalty` | Finite number from -2 to 2                                                                                                                                                           |
| `logit_bias`                            | Mapping of token-string keys to integers; at most 256 entries, keys at most 256 characters                                                                                           |
| `stop_sequences`                        | Up to 32 unique nonempty strings, at most 4096 characters each                                                                                                                       |
| `thinking`                              | Boolean, or `minimal`, `low`, `medium`, `high`, `xhigh`                                                                                                                              |
| `openai_reasoning_summary`              | `auto`, `concise`, `detailed`                                                                                                                                                        |
| `openai_store`                          | Boolean; forced false for Codex subscription                                                                                                                                         |
| `service_tier`                          | `auto`, `default`, `flex`, `priority`                                                                                                                                                |
| `anthropic_thinking`                    | `type: adaptive`, `enabled`, or `disabled`; enabled requires `budget_tokens >= 1024` and smaller than explicit `max_tokens`; optional `display: summarized`, `omitted`, or `updates` |
| `anthropic_effort`                      | `low`, `medium`, `high`, `max`                                                                                                                                                       |
| `anthropic_betas`                       | Up to 32 beta names, including `interleaved-thinking-2025-05-14`                                                                                                                     |
| `google_thinking_config`                | Optional `include_thoughts` boolean, `thinking_budget >= -1`, and `thinking_level: minimal`, `low`, `medium`, or `high`; overrides unified thinking                                  |
| `openrouter_reasoning`                  | Optional `effort: none`, `minimal`, `low`, `medium`, `high`, or `xhigh`, and `enabled` / `exclude` booleans                                                                          |

Do not use `openai_reasoning_effort`; use the supported `thinking` field. `model_configuration` accepts only an optional `base_url` for the HTTP/API-key providers offered by setup (and the legacy `openai` alias). Use an HTTP(S) URL without embedded credentials, query parameters, or fragments. Local HTTP endpoints are supported. Subscription endpoints cannot be overridden. Unknown construction fields and unsupported routes fail validation rather than silently reaching a provider.
