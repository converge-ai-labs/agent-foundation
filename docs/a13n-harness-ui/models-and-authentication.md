---
title: Models and authentication
description: Configure API keys and OAuth subscriptions (Codex, ChatGPT, Grok, GitHub Copilot), set reasoning effort, and manage context windows in Harness UI.
---

A **Model** is a reusable provider connection with request settings (`models/*.yaml`). An **Agent** selects a Model by ID and adds instructions and tools. Credentials are stored separately from YAML.

## Create and manage Models

Add a new Model in **Settings → Models** (WebUI) or via CLI:

```console
a13n-harness-ui add model
```

1. Choose an API provider/protocol or a subscription (ChatGPT, Codex, Grok, or GitHub Copilot).
2. Connect credentials: pick a saved key, enter a new key, reference an environment variable, or choose a subscription account group.
3. Select a suggested model or enter a provider model ID, review defaults (reasoning, speed tier, context budget), and save.
4. Select the Model in a conversation (`/model` in TUI or the composer footer in WebUI) and send a short prompt to verify connectivity.

In the TUI:

| Command                            | Effect                                                 |
| ---------------------------------- | ------------------------------------------------------ |
| `/agent` or `/agent agent-primary` | Select the full Agent configuration                    |
| `/model` or `/model model-primary` | Override the Model and remember it for this Project    |
| `/model default`                   | Clear the Model override and remembered Project choice |
| `/thinking` / `/thinking default`  | Inspect or reset reasoning effort for the next Run     |
| `/status`                          | Check active Agent, Model, and usage                   |

## Codex Model example

1. Sign in with `a13n-harness-ui login codex`.
2. Save `models/codex.yaml` beside your root config (`a13n-harness-ui config path`):

```yaml title="models/codex.yaml"
schema_version: "1"
kind: model
id: model-codex
name: Codex
route: openai-codex:gpt-6.2-sol
authentication:
  kind: codex_subscription
settings:
  openai_reasoning_effort: high
  openai_reasoning_summary: detailed
  openai_store: false
model_characteristics:
  context_window_tokens: 350000
  proactive_context_management_threshold: 0.65
  compact_threshold: 0.90
```

3. Run `a13n-harness-ui config validate`, then select `/model model-codex` and test a prompt.

## Model file reference

Every Model file requires `schema_version: "1"`, `kind: model`, a unique `model-` ID, and `name`:

| Field                   | Default  | Purpose                                                           |
| ----------------------- | -------- | ----------------------------------------------------------------- |
| `route`                 | Required | Provider and model ID (e.g., `openai-responses:gpt-5`)            |
| `authentication`        | Required | Credential source matching the route                              |
| `settings`              | `{}`     | Request parameters (reasoning, `max_tokens`, `temperature`, etc.) |
| `model_configuration`   | `{}`     | Connection wiring (`base_url`, `session_affinity_header`)         |
| `model_characteristics` | `null`   | Local context budget, compaction thresholds, and media policy     |

API-key authentication takes either an environment variable or a saved key (`a13n-harness-ui auth key set key-primary`):

```yaml
authentication:
  kind: api_key
  env: OPENAI_API_KEY # or credential_ref: key-primary
```

Subscription authentication uses `chatgpt_subscription`, `codex_subscription`, `grok_subscription`, or `copilot_subscription` (with optional `group` for Codex/Grok).

### Context and modality policy

Configure local budgets and media capabilities under `model_characteristics`:

| Field                                    | Default    | Meaning                                                                                                           |
| ---------------------------------------- | ---------- | ----------------------------------------------------------------------------------------------------------------- |
| `context_window_tokens`                  | `null`     | Local working context budget                                                                                      |
| `proactive_context_management_threshold` | `0.65`     | Fraction at which context reminders trigger                                                                       |
| `compact_threshold`                      | `0.90`     | Fraction at which compaction triggers                                                                             |
| `capabilities`                           | `[]`       | Binary input types: `image_understanding`, `audio_understanding`, `video_understanding`, `document_understanding` |
| `image_input`                            | defaults   | Image limits (`max_images`, `image_retention_ratio`, `max_image_bytes`, `support_gif`); `null` disables           |
| `video_input.max_video_bytes`            | `10485760` | Base64-encoded video budget per video and per request                                                             |

## Native request settings and connection wiring

Use `settings` for provider parameters (`max_tokens`, `temperature`, `thinking`, `openai_reasoning_effort`, `anthropic_thinking`, `google_thinking_config`, `service_tier`, `openai_transport: websocket|http`, `extra_headers`, `extra_body`).

Use `model_configuration.base_url` for custom HTTP/API-key endpoints or gateways (for example, `route: google-cloud:<model-id>` with `base_url: https://gateway.example.com`). Subscription routes and native `xai:` gRPC do not accept custom `base_url`.

## Subscription login and API keys

| Provider       | Login command                           | Notes                                                         |
| -------------- | --------------------------------------- | ------------------------------------------------------------- |
| API keys       | `a13n-harness-ui auth key set <key-id>` | Stored in the local key store; referenced by `credential_ref` |
| Codex / Grok   | `a13n-harness-ui login codex` / `grok`  | Browser OAuth; supports named account groups                  |
| ChatGPT        | `a13n-harness-ui login chatgpt`         | OAuth callback-URL paste flow                                 |
| GitHub Copilot | `a13n-harness-ui login copilot`         | Device authorization flow using the public Copilot App ID     |

In **Settings → Accounts → Subscription accounts**, choose **Sign in again** when Codex has rejected your authorization. If only the usage refresh failed, choose **Retry refresh**. The card shows when it last obtained the saved allowance.

### GitHub Copilot subscription

Run `a13n-harness-ui login copilot`, open the printed GitHub verification URL, and enter the device code. No custom OAuth App registration is required. Use `route: github-copilot:<model-id>` with `authentication: {kind: copilot_subscription}`.

### Account store locations

Credentials and subscription groups (`subscription_groups/*.yaml`) live under your Harness UI state directory (`~/.a13n-harness-ui/` by default) with owner-only file permissions. Never put raw API keys or OAuth tokens into YAML files.

### Media understanding defaults

When an active Model cannot read images, audio, or video natively, configure fallback Model IDs in **Settings → Models** (or `/model defaults` in TUI) under `media_understanding.image`, `.audio`, and `.video` in `a13n-harness-ui.yaml`.
