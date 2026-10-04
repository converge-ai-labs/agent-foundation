---
title: Configuration reference
description: The root configuration file, loading precedence, and when edits take effect.
---

Harness UI uses YAML and Markdown files. **The root file holds application-wide settings and defaults, including tool switches, shell review, and built-in subagents; Model and Agent files define each Agent's Model, instructions, and tools.** Start with `a13n-harness-ui setup`, then edit those files as needed.

For worked examples, start with [common configuration recipes](configuration-recipes.md). This page is the root-file and loading reference; here a conversation means a root Thread as the TUI and WebUI show it. The [built-in configuration Skill](skills-and-content-plugins.md#built-in-configuration-skill) gives the Agent offline guidance matching its installed release.

## Find the right setting

| I want to configure…                                      | Open…                               | Reference                                                                        |
| --------------------------------------------------------- | ----------------------------------- | -------------------------------------------------------------------------------- |
| Startup checks and logging                                | `a13n-harness-ui.yaml` → `process`  | [Process settings](#process-settings)                                            |
| Default Agent, Environment, plugins, or MCP               | `a13n-harness-ui.yaml` → `defaults` | [Default selections](#default-resource-selections)                               |
| Theme and output detail                                   | `a13n-harness-ui.yaml` → `display`  | [Display settings](#display-settings)                                            |
| Questions, CodeAct, built-in children                     | Root `tools` and `subagents`        | [Built-in tools](#built-in-tools-and-subagents)                                  |
| Provider, API key reference, endpoint, reasoning, context | `models/*.yaml`                     | [Model fields](models-and-authentication.md#model-file-reference)                |
| Instructions, Capabilities, visible tools, children       | `agents/*.yaml`                     | [Agent fields](agents-and-subagents.md#agent-file-reference)                     |
| Local roots and Device bindings                           | `projects/*.yaml`                   | [Project fields](environments-and-projects.md#project-file-reference)            |
| Remote Device connections                                 | `devices/*.yaml`                    | [Device configuration](environments-and-projects.md#add-device-bindings)         |
| External tools                                            | `mcp/*.yaml` or `mcp/*.json`        | [MCP fields](mcp.md#mcp-field-reference)                                         |
| Harness Plugins, Environment profiles, and Run Extensions | `extensions/*.yaml`                 | [Extension fields](extensions-and-mcp.md#harness-plugin-and-run-extension-files) |

Resource references use their **`id`**, not a filename or display name. For example, `defaults.agent: agent-coder` selects the Agent whose YAML says `id: agent-coder`.

## Locate and validate your files

```console
a13n-harness-ui config path
a13n-harness-ui config show --format json
a13n-harness-ui config validate
a13n-harness-ui --config /path/to/a13n-harness-ui.yaml config validate
a13n-harness-ui config subagents --format json
```

The default root is `~/.a13n-harness-ui/a13n-harness-ui.yaml`. `--config PATH` selects a different tree, not an overlay on the default one. Put global options before the subcommand.

The root file's directory also contains:

| Path                       | Purpose                                               | Detailed reference                                                      |
| -------------------------- | ----------------------------------------------------- | ----------------------------------------------------------------------- |
| `AGENTS.md`                | Optional global contextual guidance                   | [Guidance](agents-and-subagents.md#instructions-and-guidance)           |
| `models/*.yaml`            | Reusable Models and credential references             | [Models](models-and-authentication.md#model-file-reference)             |
| `agents/*.yaml`            | Agent definitions and child rosters                   | [Create an Agent](agents-and-subagents.md#create-an-agent-from-files)   |
| `subagents/*.md`           | Your lightweight child instructions                   | [Markdown subagents](agents-and-subagents.md#write-a-markdown-subagent) |
| `projects/*.yaml`          | Local roots, Device selections, and creation defaults | [Projects](environments-and-projects.md#project-file-reference)         |
| `devices/*.yaml`           | Reusable Device connection and credential references  | [Devices](environments-and-projects.md#add-device-bindings)             |
| `extensions/*.yaml`        | Harness Plugins, Environment profiles, Run Extensions | [Extensions](extensions-and-mcp.md)                                     |
| `mcp/*.yaml`, `mcp/*.json` | MCP server definitions                                | [MCP](mcp.md)                                                           |

Harness UI scans only files directly inside each resource directory: `.md` files in `subagents/` (except `README.md`), `.yaml` files in the other directories, and also `.json` files in `mcp/`. File extensions are case-sensitive. Filenames are for people; resource references use `id`. One file defines one resource, except MCP's multi-server `mcpServers` format. MCP environment/header values accept literals and environment references; see [MCP configuration](mcp.md). Supported-version unknown additive fields are preserved with warnings, **not applied**; unsupported schema versions, duplicate IDs/keys, YAML aliases/anchors and invalid references still reject the candidate. There is no recursive scan or ancestor configuration merge.

### Skipped Capabilities

A missing, ambiguous, unloadable, or invalid Capability in an Agent produces a warning instead of blocking conversations. Harness UI skips only that entry, keeps valid entries (including other `NativeTool` entries), and leaves your YAML unchanged. The warning names the Agent ID, Capability key, and reason. The TUI displays these warnings; `config validate` and the HTTP API's App status expose them as `capability_warnings`. Validation still succeeds when these are the only problems.

Correct the Capability name or arguments, install its trusted implementation if needed, or remove the entry. If an explicitly configured default Capability is invalid, it stays skipped rather than being replaced with broader defaults. Permission policy is never silently skipped: an invalid `ToolPermissionsCapability`, including a missing reviewer Model, rejects validation and Run composition whether or not the root shortcut is enabled. Repair the Agent policy, `security.shell_review`, or its referenced Model. Environment permissions, mandatory invocation policy, and tool switches still apply.

Only valid selections are captured for a new Run. Already captured Runs do not change, and runtime/model-provider failures are not converted into configuration warnings. Invalid YAML structure, Model resources, and Environment or Plugin configuration still require repair.

## Starter root document

This starter document shows the main root settings. Optional integrations are described below. Replace resource IDs with your own, or leave those selections `null`.

```yaml
schema_version: "1"
max_goal_iterations: 10
process:
  pricing_auto_update: true
  terminal_update_check: true
  log_level: INFO
  log_format: pretty
  max_object_bytes: 268435456
input:
  long_text_threshold_chars: 8000
memory:
  enabled: true
  auto_organize:
    enabled: true
    model: null
    instructions: ""
media_understanding:
  image: null
  video: null
  audio: null
defaults:
  project: null
  agent: null
  environment_profile: environment-native
  harness_plugins: []
  environment_run_extensions: []
  mcp_servers: []
mcp:
  host_owned_servers: []
  protocol_overrides: {}
display:
  theme: auto
  mode: concise
  show_status: true
  max_tool_result_lines: 5
  max_tool_argument_chars: 8192
tools:
  enable_ask_user_question: true
  interaction_timeout_seconds: 120
  enable_codeact: true
security:
  shell_review:
    enable: false
    risk_threshold: null
    on_flagged: null
    on_error: null
    model: null
subagents:
  include: []
webui:
  allowed_origins: []
  sidekick: {}
```

### File memory

File memory is enabled by default in CLI and WebUI. It stores cross-conversation preferences and stable facts under `memory/global/` beside the selected root YAML, plus Project-specific files under `memory/projects/<project-id>/`. A conversation without a Project uses only global memory. `MEMORY.md` is a concise always-loaded index; detailed topics can use separate files. Memory files are not configuration resources or conversation history. Broader Full Control and human filesystem access remain unchanged.

Configure **Settings → General → Memory**, or edit:

```yaml
memory:
  enabled: true
  auto_organize:
    enabled: true
    model: model-primary
    instructions: Keep decisions concise and preserve useful source references.
```

Use `instructions` for preferences such as summary language and topic grouping. The organizer reports its changes and preserves uncertainty and user corrections. Agents treat memory as historical context.

**Organization model** follows the Model on `defaults.agent` when `model` is omitted or null. Select a Model ID to override it. Organization needs one of these Models configured; it uses only scoped memory tools, not the Agent's other tools or instructions. Setup enables memory and organization while preserving existing choices.

Automatic organization is **WebUI-only**. Each new input to a WebUI conversation can start background organization of changed memory files in that conversation's scopes; organization runs in parallel with the Run. It does not read old conversations or sweep other Projects. Each scope keeps one read-only Memory Thread, separate from your ordinary conversations. Its only tools are scoped memory tools; your Agent's other tools and instructions are not inherited. It may make additional model requests and consume quota or incur cost. General settings shows current-process availability, activity, outcomes and reported usage.

Unchanged, empty, busy, or cooling-down memory makes no background model request. A successful attempt has a one-hour cooldown; failure, cancellation, or a crash leaves a fifteen-minute retry delay. Retries require another input. Each attempt is bounded to twelve requests and five minutes. New inputs do not interrupt an active organizer. Disabling either switch or stopping the server cancels maintenance, retaining partial file edits for a later fresh attempt. Foreground Runs already admitted keep their captured memory setting.

To keep memory without automatic requests, set `auto_organize.enabled: false`. Set `memory.enabled: false` to stop binding memory on later Runs; neither option deletes files. Back up the selected configuration root's `memory/` directory separately from the application data root. `.a13n-memory/` contains internal locking and organization state, not user memory. Optional Git supplies only a bounded diff hint over one snapshot; it is not required, does not touch your repository, and stores no revision history.

### Media understanding

`media_understanding.image`, `.video`, and `.audio` select saved Model IDs for file `view` fallback when the active Model cannot accept that media natively. Each defaults to `null`, preserving the corresponding Harness environment-variable fallback. Configure these in **Settings → Models** or `/model defaults`. See [media understanding defaults](models-and-authentication.md#media-understanding-defaults) for precedence, model input capability requirements, and Run capture behavior.

### WebUI allowed origins

`webui.allowed_origins` adds public request addresses to the listener's existing bind-address and loopback admission rules. It defaults to `[]`. Each entry is an exact HTTP(S) origin, including any non-default port, or the literal `"*"` to allow any request address. A trailing `/` is accepted and normalized away; default ports (`80` for HTTP, `443` for HTTPS) are normalized. Paths, credentials, queries, fragments, and partial wildcards such as `https://*.example.com` are not supported.

```yaml
webui:
  allowed_origins:
    - "https://anui.wh1isper.top:8090/"
```

To explicitly disable address restrictions, use `allowed_origins: ["*"]`. This does not disable API-key authentication or the browser's same-origin check, and it is not a CORS allowlist. Even two configured origins cannot make cross-origin API requests to each other. Prefer exact entries when public addresses are known; `"*"` removes the Host restriction, including its protection against DNS rebinding. Existing bind-address and loopback access remains allowed independently of these entries.

The listener captures this setting at startup. Restart WebUI after editing it; accepting a configuration reload does not change an active listener's access boundary. See [reverse proxies](webui.md#reverse-proxies-and-public-addresses) for HTTPS forwarding and proxy trust.

### MCP lifetime and protocol

Root `mcp.host_owned_servers` selects server IDs whose clients outlive a logical Run in CLI and WebUI; it does not add them to Agent tool selections. `mcp.protocol_overrides` maps existing server IDs to `auto`, `legacy`, or `2026-07-28`; omitted IDs use SDK automatic negotiation. Defaults are `[]` and `{}`, preserving existing configuration. See [MCP connection lifetime](mcp.md#connection-lifetime-and-protocol) and [human input](mcp.md#human-input-from-mcp-servers). These settings are captured for later Runs; live clients and input answers are not configuration or continuation state.

### WebUI MCP Apps

Interactive MCP results are opt-in. Set `webui.mcp_apps.enabled: true` and select server IDs under `webui.mcp_apps.servers`. WebUI automatically adds them to every root and child Agent alongside its generic MCP selection, deduplicating IDs without changing Agent files or saved Thread selections. CLI remains generic-only. Restart WebUI when enabling Apps or changing its separate-origin sandbox listener. See [MCP Apps](mcp-apps.md) for the field reference, interaction permissions and local, Docker and reverse-proxy configuration.

### WebUI Sidekick

Sidekick is a WebUI preference that adds instructions for independent work to root Agents and selects the Agent and Model for Threads they create with `create_thread`. It is enabled by default. Setup writes `webui.sidekick: {}` explicitly in new configuration files. Existing files that omit `webui` or `sidekick` also enable it without being rewritten. An existing `sidekick: null` remains disabled; setup preserves explicit null and custom Agent/Model selections.

In **Settings → General → Sidekick**, select **Enabled**, optionally choose an Agent and a default Model, then **Save changes**. This sets preferences for independent work without changing your default conversation Agent:

```yaml
webui:
  sidekick:
    agent: null              # Inherit the calling Agent
    model: model-worker      # Override its Model for the requested Run
```

Use existing resource IDs. Set `agent: agent-worker` to select a different Agent; either choice can use a default Model. Omit/null `model` to follow the selected Agent's current Model. An empty `sidekick: {}` enables inherited Agent selection. The Host saves the configured Model as `default_model_id` when the Agent creates a Sidekick Thread, so follow-up messages and resumed turns keep using it. An explicit `create_thread(model_id=...)` or Run picker selection overrides only that Run; it does not change the saved default. Existing Threads are not rewritten when Sidekick settings change or are disabled. Choose **Disabled** or set `sidekick: null` to turn off the extra instructions. Saving does not start any work. New WebUI Runs receive the preference; active Runs keep their captured instructions. TUI Runs and delegated children are unaffected. Generic Thread, Project, Agent and Model discovery tools remain available in WebUI whether Sidekick is enabled or not. See [Thread collaboration](webui.md#agent-collaboration-and-sidekick) for behavior and delivery limits.

### Shell review shortcut

`security.shell_review.enable` defaults to `false`: no automatic permission/reviewer injection. Setup normally initializes it to `true`. Disabled means the shortcut is unused, not that explicit Agent policies are removed.

When enabled, `risk_threshold` accepts `low`, `medium`, `high`, or `extra_high`, and `model` names a configured Model resource. `on_flagged` accepts `deny` or `approval_required`; `on_error` accepts `deny`, `approval_required`, or `allow`. Omitted/null fields inherit the Agent review policy, falling back to `extra_high`, the effective Agent Model, `approval_required` for flagged calls, and `allow` for non-timeout errors. Explicit shortcut fields take precedence during composition and preserve unrelated Agent rules. The shortcut opts in only `environment.shell_exec`, across root and child Agents. See the [shell-review recipe](configuration-recipes.md#configure-shell-review) for defaults, merging, and failure behavior. These settings affect later Run captures, not active Runs.

### Process settings

These settings take effect when the application starts; restart after changing them.

| Field                           | Default  | Meaning                                                                             |
| ------------------------------- | -------- | ----------------------------------------------------------------------------------- |
| `process.pricing_auto_update`   | `true`   | Download updated model prices for cost estimates in later Runs                      |
| `process.terminal_update_check` | `true`   | Check for a package update at TUI startup; installation still requires confirmation |
| `process.log_level`             | `INFO`   | `CRITICAL`, `ERROR`, `WARNING`, `INFO`, or `DEBUG`; normalized uppercase            |
| `process.log_format`            | `pretty` | Noninteractive logging: `pretty` or `json`; interactive diagnostics use files       |

`process.max_object_bytes` defaults to `268435456` (256 MiB), with an allowed range of 1 KiB through 1 GiB. It limits each complete **uncompressed** immutable storage object, including continuation checkpoints; it is not a Thread disk quota or a model context limit. Long coding Threads retain display history and file-edit evidence even after model-context compaction. If a checkpoint exceeds this limit, raise it (for example to `536870912` for 512 MiB) and restart the application before continuing. Larger limits increase peak memory use during serialization and validation. No history is truncated to fit, and a failed save leaves the previous selected checkpoint unchanged. Lowering the limit can prevent reading previously saved larger objects.

Use `--no-update-check` for a one-invocation override. See [updates and logs](automation-and-troubleshooting.md#logs-updates-and-exit).

### Goal checks

Root `max_goal_iterations` defaults to `10` and accepts an integer. It limits additional checks after the initial response for `/goal` and the WebUI Goal toggle. Values at or below zero disable automatic follow-ups. Each new Goal captures its limit; changing this setting does not reset a suspended Goal or change an active one's budget. Native request, tool, and token limits still apply. See [Work toward a Goal](everyday-use.md#work-toward-a-goal) for completion and recovery behavior.

### Long-text inputs

`input.long_text_threshold_chars` defaults to `8000`. A user-text block longer than that many characters is automatically saved as a retained UTF-8 file. The model receives its file path and a reading instruction, **not an inline preview or summary**. Short text is unchanged. Use a positive integer to change the threshold, or `null` to keep all text inline:

```yaml
input:
  long_text_threshold_chars: null
```

The policy applies to normal root messages and messages added while a root Run is active. Each Run captures its configuration; changes affect later Runs. TUI paste folding is independent and still expands the authored text before submission.

The selected Agent must have the built-in `view` tool enabled and a readable `thread-files` mount. Without that access, the original text stays inline with a notice; no tools are enabled automatically. A file-save failure fails the submission's execution or rejects the added message. Generated input files count toward the existing eight attachments, 10 MiB per file, and 20 MiB per input limits; HTTP request limits still apply.

Original text remains available through the Thread attachment handle after restart and scratch cleanup. Model history keeps the reference, not an automatic expansion of the file. Reading content through a tool still consumes context, especially for tasks requiring the whole document. Existing history and images are not converted by this setting.

### Default resource selections

| Field                                 | Default | Meaning                                                                                          |
| ------------------------------------- | ------- | ------------------------------------------------------------------------------------------------ |
| `defaults.project`                    | `null`  | Optional Project for application callers; omit for no Project. The TUI uses its launch directory |
| `defaults.agent`                      | `null`  | Default root Agent; setup sets this to its chosen Agent                                          |
| `defaults.environment_profile`        | `null`  | Environment profile; absent selection ultimately uses `environment-native`                       |
| `defaults.harness_plugins`            | `[]`    | Ordered exact Harness Plugin IDs                                                                 |
| `defaults.environment_run_extensions` | `[]`    | Ordered exact Environment Run Extension IDs                                                      |
| `defaults.mcp_servers`                | `[]`    | Ordered exact MCP server IDs                                                                     |

Lists must be unique. Global defaults initialize new Threads; they do not silently rewrite existing Threads' sticky resource selections. Agent-level MCP and Harness Plugin selections override their corresponding defaults. A selected resource's valid file edits can still change its behavior on later Runs.

Normal setup writes only the default Agent and Environment profile, not a `project-local` resource or `defaults.project`:

```yaml
defaults:
  agent: agent-api-key
  environment_profile: environment-native
```

An application-created conversation without a Project uses its own `thread-files/tmp/` working directory. It still has attachments, global Skills when enabled, and global guidance. The TUI selects a Project from its launch directory on the first prompt or explicit resume. Resuming an existing conversation from another directory assigns it to the launch directory's Project without changing its history or other settings.

The Agent can read and write the selected configuration directory through the file-only `configuration` mount. This defaults to `~/.a13n-harness-ui`; with `--config`, it is the chosen YAML file's parent directory. It is not a Project root and does not grant shell execution through that mount. If an existing working mount already exposes the exact directory, its route is reused. Resource edits are validated before acceptance and affect later Runs; invalid edits leave the last accepted configuration active. Process settings require restart. The mount exposes the whole selected directory, so keep sensitive file contents out of messages and logs.

### Display settings

| Field                             | Default   | Allowed values / meaning                                                          |
| --------------------------------- | --------- | --------------------------------------------------------------------------------- |
| `display.theme`                   | `auto`    | `auto`, `dark`, `light`                                                           |
| `display.mode`                    | `concise` | `concise`, `detailed`                                                             |
| `display.show_status`             | `true`    | Show the status line                                                              |
| `display.max_tool_result_lines`   | `5`       | Result-preview budget, 1–200 lines; compact tool-specific rendering may use fewer |
| `display.max_tool_argument_chars` | `8192`    | Retained argument-display budget, 128–65536 characters                            |

Display defaults are read at startup. `--display` and live `/mode` override the configured mode. `/theme` changes the theme until you quit. These options affect presentation, not model reasoning or permissions.

### Built-in tools and subagents

| Field                               | Default | Meaning                                                                                                |
| ----------------------------------- | ------- | ------------------------------------------------------------------------------------------------------ |
| `tools.enable_ask_user_question`    | `true`  | Include native `ask_user_question` in newly resolved Runs                                              |
| `tools.interaction_timeout_seconds` | `120`   | Positive finite seconds per TUI interaction or complete WebUI root decision batch; not model execution |
| `tools.enable_codeact`              | `true`  | Include native CodeAct runners and explicit `store`/`load`/`forget` state tools                        |
| `subagents.include`                 | `[]`    | Ordered named built-ins: `code-reviewer`, `executor`, `explorer`                                       |

Setup writes all three `tools` fields explicitly into the selected root YAML (by default `~/.a13n-harness-ui/a13n-harness-ui.yaml`), filling omitted fields with these defaults and preserving existing values.

Global disabled tool switches take precedence over explicit Agent Capability selections. Tool allowlists still apply. Interaction expiry never chooses an answer or approves a command. See [TUI decision handling](everyday-use.md#approvals-and-questions) and [WebUI timeouts](webui.md#questions-and-approval-timeouts) for their separate waiting and recovery lifecycles.

For all built-ins use `[code-reviewer, executor, explorer]`; for a subset use, for example, `[explorer]`. Advanced setup offers all or none; normal setup includes all three. Definitions remain package-owned; inclusion does not write `subagents/*.md`. [Built-in subagents](agents-and-subagents.md#built-in-subagents) explains inheritance and name conflicts.

## What wins, and when edits apply

At startup, a missing Model referenced by an Agent or effective reviewer blocks the application. The error names its file, field and Model ID. Fix the reference or add the Model, then restart. A disabled shell-review shortcut does not require its reviewer Model.

The open App observes configuration changes and accepts a stable, complete, valid tree. This is not synchronous with an editor's save. Invalid or incomplete edits leave the last accepted configuration active and produce diagnostics. `config validate` deliberately checks the tree; `config show` reports accepted configuration, which can differ from invalid files on disk.

For a new Thread, the first match wins:

1. Explicit creation or launch choices.
2. Selected Project defaults.
3. Selected Agent defaults for Plugin and MCP selections.
4. Root YAML defaults.
5. The built-in Environment fallback.

Collections replace whole lists; an empty list selects none. Existing Threads retain exact selections until explicitly patched; editing Project defaults does not reapply them. See [Project defaults](environments-and-projects.md#defaults-for-new-conversations). Explicit temporary choices remain overrides only for the operation, TUI process, or WebUI tab that owns them. Agent and Model choices are different: `/agent` changes the Thread's Agent without writing YAML; `/model` changes the effective Model and remembers it per Project in local state, without rewriting YAML. `/model default` clears that preference. Explicit launch `--agent` and noninteractive callers do not inherit the remembered Model.

| What changed                                                                  | When it takes effect                                                                             | What stays unchanged                                                                               |
| ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| `process.*` and bootstrap paths                                               | Restart the process                                                                              | The open App's captured startup settings                                                           |
| `display.*`                                                                   | TUI backend initialization; `/mode` and `/theme` can change live presentation                    | Stored YAML is not rewritten by presentation commands                                              |
| Default Agent, Project, Environment, plugin, MCP and Run Extension selections | Initialize new Thread selections, or use an explicit supported selection change                  | Existing Threads keep their selected IDs                                                           |
| Contents of selected Model/Agent/extension resources                          | A newly captured Run uses the accepted resources                                                 | An active or already captured composition is not rebuilt                                           |
| Tool switches and built-in subagent inclusion                                 | Newly resolved Run composition                                                                   | Existing Run tool/child contracts                                                                  |
| `input.long_text_threshold_chars`                                             | Captured for a root Run, including its later steering input                                      | The active Run's input policy                                                                      |
| `tools.interaction_timeout_seconds`                                           | Read when a TUI interaction opens; captured at Run admission for WebUI batch deadlines           | Existing timers, model execution, and unanswered checkpoints retained across App restart           |
| MCP literal-bearing source files                                              | New captures use new sources; an older capture verifies its source before client construction    | Already constructed clients retain their Run-local values; changed old sources can fail validation |
| Skill content                                                                 | Catalog preparation uses the Run's current source set; files are read through Environment access | Catalog membership is Run-frozen, but file bytes are not copied into immutable composition         |

Resume restores the Thread's selected Agent against current resources and preserves an explicit Model override in the current TUI. A later TUI launch uses a resumed Thread's saved default Model when present; otherwise it loads the launch Project's last manually selected Model. It does not infer a Model from Thread history. Without a Model override, saved reasoning is restored only when the continuation used the effective Thread-default or Agent Model. `--resume` cannot be combined with Agent, Environment, or title launch overrides; resume first, then change a selection explicitly.

For multi-file edits, save the whole tree, validate it, and start the next Run after acceptance. Do not delete local state to force reload. [MCP capture](mcp.md#literal-values-and-environment-references) and [Skill source lifetime](skills-and-content-plugins.md#automatic-sources-and-precedence) explain the file-content boundaries.

## Data root and environment variables

The data root owns saved conversations, per-Project TUI Model preferences, immutable captures, logs, stored API keys, and installed Content Plugins. Selection order is:

1. `--data-root PATH`.
2. `A13N_HARNESS_UI_DATA_ROOT`.
3. `<configuration-directory>/data`.

Changing it opens separate state; it does not migrate old conversations. Relative bootstrap paths resolve from the launch directory. Project roots must be absolute after `~` expansion; a missing root can be saved but cannot be used for a Run until available.

| Input                                                       | Purpose                                                                                    |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `A13N_HARNESS_UI_DATA_ROOT`                                 | Select local data storage                                                                  |
| Variables named by `authentication.env`                     | Model API keys read from the Harness UI process                                            |
| Variables named by MCP `environment` / `headers` references | MCP environment and request values                                                         |
| `CODEX_HOME`                                                | Compatible Codex store location; default `~/.codex`                                        |
| `COPILOT_HOME`                                              | Copilot CLI store location for account reuse; default `~/.copilot`                         |
| `GROK_AUTH_PATH`, `GROK_HOME`                               | Compatible Grok file-store location                                                        |
| `GROK_AUTH`                                                 | Recognized inline Grok mode, unsupported for shared writable login; switch to a file store |
| `COLORFGBG`                                                 | Passive terminal metadata for automatic theme selection                                    |

There is no general `A13N_HARNESS_UI_*` setting override mechanism. `storage`, `envd_runtime`, and application shutdown timeouts are embedding/runtime settings, **not** root YAML sections. Web listener and authentication options are [process-local CLI arguments](webui.md), not resource configuration.

The legacy `tools.ask_user_question_timeout_seconds` input key remains accepted. Saved configuration uses `tools.interaction_timeout_seconds`. Editing a response does not restart the Host timer; expiry denies rather than approving or inventing a result.

Set `A13N_OUTBOUND_TLS_VERIFY=false` before launch only when intentionally accepting unverified destination certificates. Unset or `true` retains verification. This shared process variable is not a YAML field; see [outbound TLS verification](../a13n-harness/models.md#outbound-tls-verification) for coverage, exclusions and risks.

## Outbound HTTP proxies

Set standard environment variables before starting Harness UI; no YAML proxy setting is needed:

```bash
export http_proxy=http://127.0.0.1:8888
export https_proxy=http://127.0.0.1:8888
export no_proxy=localhost,127.0.0.1,::1
```

Uppercase forms and `ALL_PROXY` are supported. Selection and bypass matching follow `httpx2`. Host-owned Web search/scrape/fetch/download requests, remote HTTPS MCP connections and update checks honor these variables, alongside the [Model HTTP client](../a13n-harness/models.md#outbound-http-proxies). Restart the process after changing its environment. When running in a container, the proxy address must be reachable from that container.

The proxy you configure is trusted outbound infrastructure and owns destination DNS and network restrictions. Host Web tools use native HTTP connections and never pre-resolve destination hostnames or pin IP addresses, including on direct and `NO_PROXY` routes. This allows proxy-only hosts to work without local destination DNS. HTTP(S) URL validation, redirect checks, deadlines and response limits remain enabled. TLS verification is enabled by default; only the operator-controlled [outbound TLS switch](../a13n-harness/models.md#outbound-tls-verification) can opt out for owned clients. A failed proxy request does not fall back to direct.

Plaintext loopback MCP and plaintext local/provider-private Envd attachments stay direct. HTTPS Envd attachments honor proxy variables. Third-party SDK-owned transports retain their SDK's proxy behavior; daemon-initiated Envd pairing and reverse WebSocket connections are separate from the Python HTTP attachment client.

## Run configuration

Root `run_configuration` in `a13n-harness-ui.yaml` selects one immutable configuration for each accepted root Run and its children:

```yaml
run_configuration:
  allowed_hosts:
    - api.example.com
    - 'regex:(?:[a-z0-9-]+\.)*docs\.example\.com'
  extensions:
    example.reader: {images: true}
```

Omit `allowed_hosts` or set it to null for unrestricted destinations; `[]` denies all. Ordinary entries match exact normalized hostnames or IP literals; entries prefixed with `regex:` use Python regular expressions to match the entire normalized hostname. The example allows `docs.example.com` and its subdomains, not `docs.example.com.evil.test`. Use `\.` for literal dots and YAML single quotes to preserve backslashes. Invalid/empty patterns fail configuration validation. Patterns see lowercased ASCII IDNA hostnames or canonical IPs, never URLs, paths or ports; keep them simple and caller-authored. See [host rules and regular expressions](../a13n-harness/context.md#host-rules-and-regular-expressions) for examples, matching boundaries and Python/JSON escaping. Globs and CIDRs are not supported. Include every required Model, Web and MCP hostname. The check uses declared URL hostnames on direct and proxy routes, including owned redirect hops; it never resolves DNS or pins IPs. Root and child compositions retain the snapshot; edits affect later root Runs, not active or reconstructed ones. API-key Model clients and Host Web/MCP support it; opaque subscription Model transports reject restrictive configurations. Extensions are namespaced JSON values for explicitly opting-in consumers, not automatic Capability constructor settings. Arbitrary shell and trusted plugin traffic require deployment or Environment network isolation.
