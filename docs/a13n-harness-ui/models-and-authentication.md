---
title: Models and authentication
description: Connect a provider, select the saved Model, and tune requests without confusing connection and context settings.
---

A **Model** is a reusable provider connection with request settings. An **Agent** selects a Model by ID and adds instructions and tools. Model files live in `models/` beside the [selected root configuration](configuration.md#locate-and-validate-your-files); credentials live separately.

## Choose your task

- **Add a connection:** follow the creation workflow below.
- **Write YAML:** use the [complete API-key recipe](configuration-recipes.md#change-the-model-reasoning-or-context-budget) or [Codex example](#codex-model-example).
- **Change a working connection:** use the [field reference](#model-file-reference), [request settings](#native-request-settings-and-connection-wiring), or [context policy](#context-and-modality-policy).
- **Manage credentials and allowance:** see [login](#subscription-login-and-api-keys) and [accounts](#account-store-locations).

## Create and manage Models

Start with an installed Harness UI and credentials for one supported connection. For the first Agent, use [setup](setup.md); this procedure adds another Model.

1. Open **Settings → Models** in WebUI, or run:

   ```console
   a13n-harness-ui add model
   ```

2. Choose the connection: an API provider/protocol, or ChatGPT, Codex, Grok, or GitHub Copilot subscription. Subscription connections use their own login and transport.

3. Connect the account. For API access, select a stored key, save a new key in the hidden field, or name a server environment variable. For Codex/Grok, choose an account group. Login and key saves take effect independently of the Model draft.

4. Select a suggested model or enter its case-sensitive provider ID. Suggestions are release-owned starting points; use an ID your account can access. Manual entry remains available if discovery fails.

5. Review **Model defaults**: reasoning, service tier, and working context budget. **Custom budget** accepts a number; **Restore recommended defaults** deliberately replaces authored settings. Ordinary connection changes preserve explicit settings.

6. Save. The saved Model now appears in conversation selectors. Select it for a new Run and send a short prompt. In the TUI, use `/model`, then `/status` to check the selection.

A saved/validated Model proves configuration validity; the prompt verifies credentials and inference access. Saving does not change an active Run. To make the Model an Agent's permanent choice, edit that Agent's `model` field or use its Model selector. Choose native tools on the **Agent**; a Model change does not rewrite its tools.

`setup` and the new-Model path in `add agent` use the same connection preparation. You can configure a subscription before login, but authenticate before its first request. Cancelling a draft does not undo an already saved key, login, or independently saved inline Model.

## Change the Agent or Model in the TUI

| Command                            | Effect                                                 |
| ---------------------------------- | ------------------------------------------------------ |
| `/agent` or `/agent agent-primary` | Select the full Agent configuration                    |
| `/model` or `/model model-primary` | Override only the Model; remember it for this Project  |
| `/model default`                   | Clear the Model override and remembered Project choice |
| `/thinking`                        | Show the selected Model's supported reasoning controls |
| `/thinking default`                | Use the Model's saved reasoning settings               |
| `/status`                          | Inspect the selection and observed root usage          |

Use your saved IDs. Agent/Model selection clears temporary thinking, Fast, and Pro controls. The remembered Project Model persists across conversation navigation, Agent selection, and TUI restarts; explicit launch `--agent`, headless runs, and API callers do not inherit it. These commands preserve history and Environment selection and do not edit YAML.

An active operation keeps its captured values. Markdown children inherit the parent's effective Model; independently configured children and auxiliary Models retain their own. Observed cost is an estimate, not a subscription bill. See [selection and recovery](everyday-use.md#everyday-interaction) and [usage controls](everyday-use.md#tasks-usage-and-terminal-feedback).

In both TUI and WebUI, each Agent receives its current Thread ID and provider model name automatically. It can identify its Model or attribute work without inspecting configuration or asking you. This uses the current Run's resolved Model, not its local resource ID or a selection for the next Run. Child Agents receive their own Model name.

## Codex Model example

This example adds a Codex subscription Model to an existing configuration.

1. Run `a13n-harness-ui login codex` and finish authorization.

2. Run `a13n-harness-ui config path` and find `configuration_path`. Save this complete file as `models/codex.yaml` relative to that root YAML's parent directory. Replace the route's model ID if your account uses another supported model.

   ```yaml title="models/codex.yaml — complete file"
   schema_version: "1"
   kind: model
   id: model-codex
   name: Codex
   route: openai-codex:gpt-6.1-sol
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

3. Run `a13n-harness-ui config validate`. With a custom tree, use the same `--config PATH` before each subcommand, including login.

4. In the TUI, select `/model model-codex`, send a short prompt, and inspect `/status`. For an Agent default, set its `model: model-codex` and clear any temporary Model override.

The example uses the default Codex account group. [Account groups](#account-store-locations) let you select another group without putting tokens in YAML.

## Model file reference

Each file needs `schema_version: "1"`, `kind: model`, a unique ID beginning `model-`, and a human-readable `name`.

| Field                   | Default  | Owns                                                               |
| ----------------------- | -------- | ------------------------------------------------------------------ |
| `route`                 | Required | Provider and provider model ID, such as `openai-responses:gpt-5`   |
| `authentication`        | Required | Credential source matching the route                               |
| `settings`              | `{}`     | Request parameters, such as reasoning and output limits            |
| `model_configuration`   | `{}`     | Supported connection wiring: `base_url`, `session_affinity_header` |
| `model_characteristics` | `null`   | Local context budget and input policy                              |

These mappings belong in the **Model file**, not the root YAML. API authentication requires exactly one source:

```yaml title="Model file — read a process environment variable"
authentication:
  kind: api_key
  env: OPENAI_API_KEY
```

```yaml title="Model file — use a saved key"
authentication:
  kind: api_key
  credential_ref: key-primary
```

Save `key-primary` first with `a13n-harness-ui auth key set key-primary`. An environment reference names the variable, not the secret; that variable must be present in the Harness UI process when used. Missing credentials fail the request instead of selecting another source.

Subscription authentication uses `chatgpt_subscription`, `codex_subscription`, `grok_subscription`, or `copilot_subscription`, with the corresponding `openai-chatgpt:`, `openai-codex:`, `grok:`, or `github-copilot:` route. Codex/Grok optionally specify `group`. Subscription endpoints cannot be overridden.

### Context and modality policy

Fields inside `model_characteristics` have these defaults when the object is supplied:

| Field                                    | Default    | Meaning                                                                                                                  |
| ---------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------ |
| `context_window_tokens`                  | `null`     | Positive local working budget; omission retains native/catalog behavior                                                  |
| `proactive_context_management_threshold` | `0.65`     | Reminder fraction, 0–1; `null` disables the derived threshold                                                            |
| `compact_threshold`                      | `0.90`     | Compaction fraction, greater than 0 and at most 1                                                                        |
| `capabilities`                           | `[]`       | Binary input declarations: `image_understanding`, `audio_understanding`, `video_understanding`, `document_understanding` |
| `url_input.video`                        | `[]`       | Native URL subtypes, currently `youtube`, separate from binary video                                                     |
| `video_input.max_video_bytes`            | `10485760` | Base64-encoded video budget, per video and per request aggregate                                                         |

The budget controls local behavior, not provider entitlement. Explicit token thresholds on Agent handoff/compaction Capabilities remain authoritative. Harness UI accepts legacy `context_window`; new saves use `context_window_tokens`, and both supplied values must agree. Core Harness specs require the canonical spelling. Merely reading old files or captures does not rewrite them.

### Image input preparation

Merge this section into a Model to customize image preparation. Omit `image_input` for defaults, or set it to `null` to disable preparation:

```yaml title="Model file — image preparation"
model_characteristics:
  image_input:
    support_gif: false
    max_images: 20
    image_retention_ratio: 0.6
    max_image_bytes: 5242880
```

At overflow, this retains the newest 12 images, then allows growth to 20 again. The ratio is the retained fraction; `1` evicts only excess images. Bytes are the Base64-encoded size **per image**, not original file size or total request size. GIF support is declared policy, not detection. Unspecified fields retain native defaults; see the [full image policy](../a13n-harness/models.md#image-input-policy) for dimensions and splitting.

Root, independent child, and image-understanding Models each use their own policy. Captured Runs retain the complete policy after later file edits.

## Native request settings and connection wiring

Use `settings` for JSON-compatible request parameters. Nested objects, explicit `null`, and significant string whitespace are preserved. The installed adapter/provider validates their meaning when used; loading configuration makes no inference request.

| Setting                                                                               | Purpose                                                   |
| ------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| `max_tokens`, `temperature`, `stop_sequences`                                         | Generation controls                                       |
| `thinking`, `openai_reasoning_effort`, `anthropic_thinking`, `google_thinking_config` | Generic or native reasoning controls                      |
| `service_tier`, `openai_service_tier`                                                 | Processing tier; the native OpenAI field takes precedence |
| `extra_headers`, `extra_body`                                                         | Native request extensions                                 |
| `openai_prompt_cache_key`, `openai_store`                                             | OpenAI options, subject to subscription adapter behavior  |

Settings are persisted verbatim, including any secrets placed there. Use dedicated credential references and inspect diagnostics before sharing them.

Use `model_configuration` for Host connection construction. Supported HTTP/API-key routes accept an HTTP(S) `base_url` without embedded credentials, query, or fragment; local HTTP is supported. Unknown constructor fields fail validation. Subscriptions and native `xai:` gRPC require empty connection configuration.

For an API-key **Google Cloud gateway**, edit a complete Model's connection fields as follows. This differs from the guided `google:` Gemini Developer API connection:

```yaml title="Model file — Google Cloud gateway connection fields"
route: google-cloud:your-gateway-model-id
authentication:
  kind: api_key
  credential_ref: key-gateway
model_configuration:
  base_url: https://gateway.example.com
```

Save `key-gateway`, use the gateway's model ID and endpoint, validate, then test a request. The SDK appends version/resource paths; appending `/v1beta` or `/v1beta1` does not switch transports. `google-gla:`, `google-vertex:`, and `gemini:` are Cloud aliases. This route supplies API-key authentication, not Cloud IAM/service accounts.

## Gateway session affinity

Use affinity when your gateway has a routing rule that reads a session header. In Add Model, select **Gateway session affinity** or enter a **Session affinity header**. The saved Model contains the header's name:

```yaml title="Model file — gateway wiring"
model_configuration:
  base_url: https://gateway.example.com/v1
  session_affinity_header: x-litellm-session-id
```

Replace the endpoint and header with your gateway's settings. Keep the field in `model_configuration`, not `settings.extra_headers`. Harness derives the value from the current Thread ID as a stable UUID v5; do not supply a value yourself. Omit/null the field to disable it.

| Picker preset         | Saved header           |
| --------------------- | ---------------------- |
| LiteLLM               | `x-litellm-session-id` |
| Conversation ID       | `x-conversation-id`    |
| Bifrost               | `x-bf-session-id`      |
| X-Session-ID / custom | `x-session-id`         |

The picker configures the client, not the gateway. Verify actual routing in gateway logs; a successful model response does not prove affinity or cache reuse. Bifrost's preset addresses API-key affinity, not weighted target pinning. Native xAI gRPC and subscriptions do not expose this setting.

The same Thread keeps its value across turns/retries; children, forks, and independent Threads use their own IDs. The header name is captured per Run. See [shared derivation](../a13n-harness/models.md#model-request-affinity).

**Upgrading older files:** omitted fields now disable the formerly implicit `x-session-id`; add that name explicitly to retain it for later Runs. The old process-wide switch does not override Model recipes. Derived UUIDs also replace raw Thread IDs for automatic gateway/cache/native Codex defaults, so an existing Thread may lose upstream affinity once. Local history and IDs remain unchanged; files are not rewritten.

## Subscription login and API keys

For Codex, Grok, or Copilot, run one login command and finish the printed device-authorization flow in your browser:

```console
a13n-harness-ui login codex
```

Substitute `grok` or `copilot` for the other connection. Device authorization requires no Host callback and expires within fifteen minutes. Codex/Grok can use `--browser` when the browser can reach the Host loopback callback; Codex uses port 1455. There is no automatic method fallback, and Copilot has no browser-callback mode. [ChatGPT](#chatgpt-subscription) uses a different callback flow.

Inspect login state with `a13n-harness-ui auth status`. Replacing another identity in the selected account requires CLI `--allow-account-switch`. Inspect the selected account before logout or replacement.

For API access, save a named key through a hidden prompt:

```console
a13n-harness-ui auth key set key-primary
a13n-harness-ui auth key list
```

Then use `credential_ref: key-primary` in the Model. The wizard also accepts `key:key-primary` or `env:OPENAI_API_KEY`. Newly entered keys are saved immediately under a fresh reference. Never put keys in a conversation. `auth key delete key-primary` removes that stored key; update affected Model references first.

Keys are plaintext in private-permission `<data-root>/auth.json`. Captures store references, not key bytes. Protect the Host and backups. A successful key save/login remains even if you cancel Model creation.

### Account-store locations

Use **Settings → Accounts & API keys** to add, sign in, rename, pause, and group Codex/Grok accounts. **Ordered consumption** uses members in order; **Weighted balance** distributes Runs by positive integer weights. Select the resulting group in **Settings → Models**.

For file-based configuration, copy account IDs from the account page and save:

```yaml title="subscription_groups/work.yaml — complete file"
schema_version: "1"
kind: subscription_group
id: codex-work
name: Work accounts
provider: codex
strategy: weighted
allow_credits: false
accounts:
  - account: acct-work
    weight: 3
  - account: acct-personal
    weight: 1
```

Merge the selection into the subscription Model, validate, and use it on a new Run:

```yaml title="Model file — select the account group"
authentication:
  kind: codex_subscription
  group: codex-work
```

A Run captures group membership/policy and checks account pause, deletion, logout, and credentials before each request. Included allowance is preferred; rechargeable balance requires **Allow rechargeable credits**. Usage snapshots can lag concurrent consumption, so this is not a strict spending cap. Authentication/quota failures can switch accounts before response delivery; tools and visible streams are not replayed. **MAIN** shows the actual root account and reason. Auxiliary calls do not replace it. Live pins an eligible account at connection.

Credentials reside in private-permission plaintext `oauth/accounts/<account-id>.json` under the data root. `oauth/accounts.json` is the nonsecret catalog; `oauth/state/` stores usage snapshots. First upgraded startup can import supported Codex/Grok credentials once from `CODEX_HOME` or `GROK_AUTH_PATH`/`GROK_HOME`. Original files stay unchanged, and later operations use native files without re-importing. The copied refresh grant is shared: rotation in either client can invalidate the other login. Sign in separately to recover. Unsupported imports appear under **Migration from existing CLI logins**.

Omitted groups select `codex-default` or `grok-default`, initially containing a migrated account. Deleting a default group is respected; restore it or select another group.

**Refresh allowance.** Codex responses update reported windows immediately. Before requests, usage API observations at least 60 seconds old are refreshed; idle accounts refresh every ten minutes. Reset dates do not schedule polling. Use **Refresh** or `/usage accounts` immediately, and `/usage subscription` for cached summaries. Failed refreshes retain the last observation with its timestamp/error. TUIs sharing the data root eventually share those observations.

Cards show each provider's own allowance: Codex duration windows, model/monthly limits, vouchers, and rechargeable balance; Grok billing allowance and purchased USD credits; Copilot returned quota buckets; ChatGPT a link to official usage settings rather than numeric allowance. Missing/failed data means unavailable, not zero. Copilot refreshes on entry/every ten minutes and offers **Refresh allowance**. Voucher-detail failure preserves any known count. **Reset voucher…** or `/usage reset` requires an account, voucher, and explicit confirmation; automatic refresh/failover never redeems one.

## ChatGPT subscription

1. Choose **ChatGPT subscription** in Add Model, or run `a13n-harness-ui login chatgpt`.
2. Open the sign-in link and authorize. In the CLI, paste the **entire callback URL** into its hidden prompt, even if the browser's loopback page cannot load. WebUI first tries the callback automatically; for a remote Host, expand **Callback cannot reach this server?** and paste there.
3. Save a Model with a discovered account slug, then select it and test a prompt.

```yaml title="models/chatgpt.yaml — complete file; replace the slug"
schema_version: "1"
kind: model
id: model-chatgpt
name: ChatGPT Subscription
route: openai-chatgpt:YOUR_ACCOUNT_MODEL_SLUG
authentication:
  kind: chatgpt_subscription
settings: {}
model_configuration: {}
```

The callback contains a one-time code: keep it out of conversations and shared diagnostics. It is validated/exchanged, never fetched as a URL. Invalid URLs can be corrected; exchange failure needs a new login. This is not a device-code flow.

The native transport sends full history/developer instructions through Responses with `store: false` and `stream: true`, collecting the required stream for ordinary calls. Its profile omits generic temperature, Top P, and output-token limits. Static API keys and endpoint overrides are unsupported. Account catalog visibility still needs an inference test.

Credentials occupy a separate namespace in the Host's `auth.json`, not Codex storage. `auth logout chatgpt` removes local tokens but retains registration; manage remote authorization in account settings. Active Models can retain cached tokens. Expiry/401 refresh saves the full credential set without cross-process refresh exclusion. Replacing this Host account affects all its users.

## GitHub Copilot subscription

Choose **GitHub Copilot subscription**, authorize with device login, and select **Fetch account models** or enter an ID manually. Fetching lists authenticated Chat Completions models without inference. Native login needs no installed Copilot CLI, app registration, or client secret. Review the requested `read:user`, `read:org`, `repo`, and `gist` scopes on the authorization screen.

```yaml title="models/copilot.yaml — complete file; replace the model ID"
schema_version: "1"
kind: model
id: model-copilot
name: Copilot
route: github-copilot:YOUR_MODEL_ID
authentication:
  kind: copilot_subscription
settings: {}
model_configuration: {}
```

Select the saved Model and send a prompt to verify actual access. Copilot uses its native profile rather than Codex Fast/context/tool presets or an automatically chosen reviewer. Setup, login, saving, and an empty conversation do not issue inference.

Native credentials and selected account/source live in private-permission plaintext `<data-root>/oauth/copilot.json`. **Choose account source** can instead reuse the official GitHub.com CLI file when `$COPILOT_HOME/settings.json` explicitly enables `storeTokenPlaintext: true`; default home is `~/.copilot`. Supported legacy string/object token forms and snake_case aliases are read from `config.json` without copying credentials. Compatibility tests use synthetic CLI 1.0.88 Linux files; OS keychain storage has no stale-file fallback. Prefer native login over weakening CLI storage.

```console
a13n-harness-ui auth sources copilot
a13n-harness-ui auth select copilot --source native --account YOUR_LOGIN
a13n-harness-ui auth status copilot --format json
```

Use `--source copilot_cli_file` to select the file source explicitly. Missing/expired credentials never select another account automatically.

> [!WARNING]
> `a13n-harness-ui auth logout copilot` deletes the selected credentials. With the CLI-file source, this also affects the official CLI and other Hosts using that file. Other accounts remain; restore the selected credentials or select another source. Logout does not revoke GitHub authorization. It preserves unrelated file data but normalizes comments/formatting to JSON.

## API context defaults and readable names

New API connections start with a **350,000-token** working budget, or a smaller known catalog window. Unknown IDs also start at 350,000 as a local default. Enter a positive count such as `128000` or `128k` and keep it within your endpoint's actual capacity.

The saved reminder and compaction fractions are 65% and 90%: 227,500 and 315,000 tokens at 350k. They use the latest root request footprint, not cumulative usage, with standard Harness summary prompts. Reusing a Model preserves its policy.

Generated names describe the connection and model; Agent suggestions add **- Coding**. Add commands avoid name collisions and permit custom Unicode names. Names are labels; references still use IDs, and existing resources are not renamed.

## Codex reasoning and context

Advanced setup offers local budgets **standard: 272,000**, **balanced: 350,000** (default), and **extended: 872,000**. Choose a budget your endpoint/account supports. It controls reminder/compaction timing rather than raising provider limits.

`/thinking` shows adapter-supported effort levels, token budgets, or Off. `/thinking default` restores the Model configuration. Reasoning strength is independent of display detail. The status line reports requested settings; only provider-exposed reasoning content is displayed.

Codex strips unsupported generic settings such as `max_tokens` and forces `openai_store: false`. Other explicit `openai_*` fields follow native validation. API output-budget presets are not applied to Codex subscription creation.

## Pro reasoning mode

On supported Responses/Codex Models, `/pro on` requests Pro and `/pro off` requests Standard, **not thinking off**. `/pro reset` restores the Model's configured mode, which may itself be Pro. Unset is Provider default. WebUI exposes the same independent control under **Agent & Model settings**.

For a permanent choice, merge into the selected Model:

```yaml title="Model file — reasoning mode"
settings:
  openai_reasoning_mode: pro
  thinking: high
  openai_reasoning_summary: detailed
```

Use `standard` for Standard or remove the mode field for provider default. Mode, effort, summary preference, and Fast are independent. Temporary mode survives `/new` and in-process `/resume`, clears on Agent/Model selection, and is not restored from history in a new TUI. Unsupported connections or conflicting `extra_body.reasoning` reject explicit selection. Adapter support is not account entitlement; captured Runs keep their original settings.

## Fast mode and service tiers

New Codex setup defaults to **Fast**, saving `openai_service_tier: priority`; **Standard** saves `default`. Reusing an existing Model preserves its tier.

### Temporary: use `/fast`

| TUI command       | Requested tier                    |
| ----------------- | --------------------------------- |
| `/fast`           | Toggle effective priority         |
| `/fast on`        | `priority`                        |
| `/fast off`       | `default`                         |
| `/fast ultrafast` | `ultrafast` for Codex GPT-6 Astra |
| `/fast reset`     | Inherit the Model file            |

Off and reset differ when the file specifies priority. The override lasts through `/new` and `/resume` until exit or Agent/Model selection. It leaves thinking unchanged, affects inheriting Markdown children, and leaves independent/auxiliary Models alone. YAML and saved Thread configuration stay unchanged.

Fast is a request setting, not a speed guarantee. The status shows the requested tier; provider support, quota, and charges determine fulfillment. Unsupported tiers follow native behavior without a silent retry at another tier.

### Ultrafast for Codex GPT-6 Astra

For route `openai-codex:gpt-6-astra`, use `/fast ultrafast` or **Ultrafast** beside **Fast** in WebUI Agent & Model settings. The buttons are mutually exclusive; clicking the active one requests Standard, while **Use default** restores the file. HTTP operations use `fast: "ultrafast"`; boolean/null values retain their existing meaning.

Harness UI restricts this control to the supported connection, not account eligibility. Confirm access and usage terms with the provider before selecting it. The request changes neither thinking effort nor reasoning mode. To persist it, save `settings.openai_service_tier: ultrafast` in that Model.

### Permanent: edit the Model

Merge the tier into the selected Model, preserving other settings:

```yaml title="Model file — processing tier"
settings:
  openai_service_tier: priority
```

Validate, clear a temporary override with `/fast reset`, and start a new Run. `default` requests Standard; removing tier fields uses provider default. Generic `service_tier` remains an alias, but the OpenAI-specific field wins when both exist. Keep one field. Other providers use native/generic fields; `auto`/`flex` depend on adapter support.

## Native media input

New Model creation saves known input declarations from the bundled catalog. Unknown IDs get no guessed media capabilities. Exact known IDs can receive the same defaults behind custom endpoints, so verify endpoint support. Existing files are never backfilled.

To enable supported images on an existing Model, merge:

```yaml title="Model file — image input declaration"
model_characteristics:
  capabilities: [image_understanding]
```

Preserve other required declarations in the list. Validate, start a new Run, and ask the Agent to inspect a small image. These are **Model input** capabilities, separate from Agent tools/Capabilities and output modalities.

Known native Google connections can also declare audio/video and YouTube input. For an endpoint supporting them:

```yaml title="Model file — native video and audio"
model_characteristics:
  capabilities: [image_understanding, audio_understanding, video_understanding]
  url_input:
    video: [youtube]
  video_input:
    max_video_bytes: 10485760
```

`view` and `read_media_url` use native input first, then the [configured understanding Model](#media-understanding-defaults). Direct media URLs download bounded binary input; pasted links remain text. YouTube stays a native URL and has no download fallback when unsupported. No separate Agent Capability/reader is needed for the URL declaration.

Native video has a default 10 MiB encoded single/aggregate budget across local `view` and supplied video. There is no automatic compression, splitting, frame extraction, or upload. Provider 413/exact payload-size errors can remove inline images/videos and replay once; authentication, permission, MIME, rate-limit, and download errors retain their own failure handling. Request filtering/healing preserves original history.

## Media understanding defaults

1. Create saved Models declaring the required image/video/audio input capability.
2. Open **Settings → Models → Media understanding**, choose each fallback, and save. The **Set as…** menu or TUI `/model defaults` edits the same global selection.
3. Use a primary Model without that native input, start a new Run, and ask it to inspect a media file.

The root file, unlike the Model examples above, holds these references:

```yaml title="a13n-harness-ui.yaml — merge global fallbacks"
media_understanding:
  image: model-vision
  video: model-video
  audio: null
```

Substitute existing IDs. Each must declare the matching input capability; remove references before deleting a Model or removing its declaration. Selection is separate from the primary/remembered Project Model.

Native-capable primary Models receive media directly without initializing an auxiliary Model. Otherwise the chosen Model uses its own captured settings, credentials, input policy, and Thread affinity, returning text. It does not inherit temporary primary thinking/Fast overrides. Omitted/null uses the [Harness environment-variable fallback](../a13n-harness/multimedia-understanding.md); clearing a selection does not disable that fallback. A configured Model's failure is reported, not silently replaced.

Saving checks references/declarations without provider requests. Credentials load only when needed; charges may apply. Captured/recovered Runs and children keep their resolved recipes. This controls file understanding, not conversion of unsupported composer attachments or forced proxying of native input.

## Starter model choices

The installed release owns suggestions shown by setup and Add Model. Select an offered route or enter a case-sensitive ID from your provider. Suggestions do not query account entitlement; API and subscription access can differ. Use a first prompt to check the actual connection instead of treating a catalog entry as a successful test.

A separately created **Codex shell review** or **Grok shell review** Model serves review without a separate Agent. Setup preserves user-edited reviewer resources. API setup enables the shortcut only when you choose a reviewer. See [shell review](configuration-recipes.md#configure-shell-review).

## API providers and settings presets

Choose the provider's native connection when available. Dedicated `deepseek:`, `zai:`, and `moonshotai:` routes preserve provider reasoning content through tool continuation and later turns; generic Chat Completions can lose that behavior. xAI offers `grok:` Chat Completions and `xai:` native gRPC with native tools. Both API-key choices are separate from Grok subscription login.

For another compatible HTTP service, choose **OpenAI-compatible · Chat Completions**, then enter its URL and model ID. Cloud IAM and subscriptions need their own transports. For Cloudflare AI Gateway, use its account API URL ending `/ai`, a token with Workers AI Read permission, and a publisher-prefixed model ID. Optional `settings.extra_headers.cf-aig-gateway-id` selects the gateway; otherwise third-party models use the default alias. Configure BYOK or unified billing on the gateway. This connection exposes Chat Completions, not the separate Workers AI catalog/native server tools.

Presets write ordinary settings at **creation time**. The picker shows reasoning and output limits before saving. Provider defaults avoids adding a preset output cap; native defaults can still limit output. Existing files and explicit settings mappings, including an empty mapping, are preserved.

- **OpenAI Responses:** recognized reasoning profiles offer effort and returned-summary controls; Chat Completions omits Responses-only summary fields.
- **Anthropic:** native profiles choose adaptive, extended, or interleaved thinking. Unknown IDs do not receive guessed thinking controls.
- **Google:** effort maps to supported levels/budgets and requests available summaries.
- **OpenRouter:** reasoning presets request returned reasoning with `exclude: false`.
- **DeepSeek/GLM/Kimi:** recognized models offer **Thinking · preserved**; GLM also writes `zai_clear_thinking: false`. A generic Off choice is not assumed for always-thinking or differently controlled models.

Returned reasoning is the provider's exposed content or summary. Native settings and endpoint acceptance remain the adapter/provider's responsibility.

### Anthropic cache defaults

Anthropic creation presets save explicit block-cache flags, including custom IDs/endpoints:

```yaml title="Anthropic Model — cache fields saved by presets"
settings:
  anthropic_cache_instructions: true
  anthropic_cache_tool_definitions: true
  anthropic_cache_messages: true
```

These replace unified cache controls for this connection's presets. Explicit false values, durations, or an authored empty settings mapping remain authoritative. Claude through another protocol does not automatically get these flags. Existing files need a deliberate edit or preset application.

Recognized adaptive profiles save `anthropic_thinking: {type: adaptive, display: summarized}` and high effort. Adaptive presets omit interleaved-thinking, context-management, and 1M-context beta headers. If your gateway requires a beta, configure `settings.anthropic_betas` explicitly. Models with always-on thinking do not offer Off.

### Paired output budgets

Some reviewed API model IDs pair a thinking preset with `settings.max_tokens`. The picker displays the exact value; review it against your endpoint and expected response before saving. This is a **per-request output cap**, not a total Run limit or a target length. Reasoning may consume part of it. Context reminders/compaction are separate and do not resize the cap.

For example, reviewed OpenAI effort presets use 16,384/32,768/65,536 for low/medium/high-or-xhigh; reviewed Claude adaptive presets use 32,768, while explicit 8,192-token extended thinking keeps 16,384 output. Gemini 2.5 high uses a 24,576-token thinking budget within a 32,768 output cap. Applicability is by exact reviewed model and native profile, not a model-name prefix; unreviewed adaptive Claude retains the 16,384 baseline. Other unreviewed routes gain no new cap. Subscription creation does not use API presets.

Edit `settings.max_tokens` independently later. `/thinking` does not recalculate it, nor does loading/reusing a Model reapply a preset. Custom endpoints can have different limits. Native serialization determines the wire field; the installed DeepSeek adapter emits `max_completion_tokens`, whose live acceptance was not established by local mock-response tests. Validate actual endpoint behavior before relying on that cap.
