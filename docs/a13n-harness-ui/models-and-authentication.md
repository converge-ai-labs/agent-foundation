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

For API access, run the hidden key prompt first, then choose `api` in setup and provide `key:key-primary`. Or choose `env:OPENAI_API_KEY`; enter the variable name, not its value. The variable must exist in the Harness UI process. Never paste an API key into the normal composer.

Stored API keys are plaintext in the data root's independent `auth.json`, with private permissions. Protect the host and backups. Configuration and Run snapshots hold references, not key bytes. A completed credential save/login is independent of setup publication and is not undone by cancelling setup.

## Codex reasoning and context

The defaults are release-owned recommendations, not claims that every account supports every model or context size.

| Setup choice | Working context budget | When to choose it                                                                             |
| ------------ | ---------------------: | --------------------------------------------------------------------------------------------- |
| standard     |                272,000 | Conservative local budget matching the current Codex catalog default                          |
| balanced     |                350,000 | Default for repository work                                                                   |
| extended     |                872,000 | Large tasks where your account supports the catalog maximum; expect greater latency and usage |

A **working budget** controls local reminders and compaction. It does not increase the provider's limit or grant access. The default reminder threshold is 65% and automatic compaction starts at 90%, based on the latest reported root request footprint rather than cumulative tokens. At 350k these are 227,500 and 315,000 tokens.

Reasoning choices are `low`, `medium`, `high`, and `xhigh`. `/thinking default` returns to the selected Model's configured value. High reasoning is independent of detailed display: you can use high reasoning while seeing concise output. Only provider-exposed reasoning is shown, and some providers do not return it.

Codex subscription requests do **not** receive an API output-token cap copied from YAACLI presets. The native subscription adapter strips unsupported settings such as `max_tokens`; `openai_store` is forced false.

## Codex Model example

Save this as `models/codex.yaml` beside the root configuration.

`models/codex.yaml`:

```yaml
schema_version: "1"
kind: model
id: model-codex
name: Codex coding
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
| `model_configuration`   | `{}`     | Reserved construction mapping; must be empty in this adapter release                           |
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

## Change models during a conversation

`/model` lists configured resources. `/model model-primary` and `/thinking low` apply to subsequent operations without editing files. `/model default` and `/thinking default` return their axes to configured defaults. An in-flight operation keeps its captured values. An inherited Markdown child receives the parent's effective recipe; an independently referenced Agent keeps its own Model.

`/status` shows observed root usage and, for Codex, read-only subscription limit information plus separately confirmed credit redemption. Local observed cost is an estimate, not your subscription bill. See [usage and credit confirmation](everyday-use.md#tasks-usage-and-terminal-feedback).

## Supported request settings

All entries in `settings` are optional; omitted or `null` values leave provider/native defaults. This is the complete Harness UI adapter surface, not an unrestricted pass-through of every upstream provider option. A syntactically accepted option may still be unsupported by a particular model or subscription.

| Setting                                 | Accepted value                                                                             |
| --------------------------------------- | ------------------------------------------------------------------------------------------ |
| `max_tokens`                            | Integer at least 1; Codex subscription removes unsupported output caps                     |
| `temperature`                           | Finite number                                                                              |
| `top_p`                                 | Number from 0 to 1                                                                         |
| `top_k`                                 | Integer at least 1                                                                         |
| `timeout`                               | Positive finite seconds                                                                    |
| `parallel_tool_calls`                   | Boolean                                                                                    |
| `tool_choice`                           | `none`, `required`, `auto`                                                                 |
| `seed`                                  | Integer                                                                                    |
| `presence_penalty`, `frequency_penalty` | Finite number from -2 to 2                                                                 |
| `logit_bias`                            | Mapping of token-string keys to integers; at most 256 entries, keys at most 256 characters |
| `stop_sequences`                        | Up to 32 unique nonempty strings, at most 4096 characters each                             |
| `thinking`                              | Boolean, or `minimal`, `low`, `medium`, `high`, `xhigh`                                    |
| `openai_reasoning_summary`              | `auto`, `concise`, `detailed`                                                              |
| `openai_store`                          | Boolean; forced false for Codex subscription                                               |
| `service_tier`                          | `auto`, `default`, `flex`, `priority`                                                      |

Do not use `openai_reasoning_effort`; use the supported `thinking` field. `model_configuration` must currently be `{}`. Unknown construction fields and unsupported routes fail validation rather than silently reaching a provider.
