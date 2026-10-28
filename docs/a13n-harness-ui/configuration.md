---
title: Configuration reference
description: Find the owning file, check a configuration change, and look up root settings.
---

Use this page when you know which setting you want to change. For a first working connection, follow [setup](setup.md). For a complete edit-and-test procedure, use [configuration recipes](configuration-recipes.md).

Harness UI keeps three locations separate: **configuration** describes resources, **data** stores conversations and credentials, and a **Project** selects working directories. Running the CLI inside a repository does not make that repository the configuration directory.

## Locate and validate your files

1. Find the selected root YAML:

   ```console
   a13n-harness-ui config path
   ```

   The default is `~/.a13n-harness-ui/a13n-harness-ui.yaml`. Its parent directory owns the resource files listed below.

2. Edit the owning file, then check the complete configuration tree:

   ```console
   a13n-harness-ui config validate
   a13n-harness-ui config show --format json
   ```

   Validation reports configuration errors and Capability warnings. It does not test a provider connection. `config show` reports accepted configuration; a running App can keep the previous valid version while a disk edit is invalid.

3. Apply the change at the boundary described in [when edits apply](#what-wins-and-when-edits-apply), then test the affected behavior.

To use another tree, pass `--config` before the subcommand on every command targeting that tree:

```console
a13n-harness-ui --config /path/to/a13n-harness-ui.yaml config validate
```

The path must end in lowercase `.yaml`. It selects a complete tree, not an overlay on the default configuration.

## Find the right setting

Paths in this table are relative to the root YAML's directory. Resource selections use the YAML **`id`**, not the filename or display name.

| File or directory            | What belongs there                                                                                                     |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `a13n-harness-ui.yaml`       | Process settings, defaults, display, tool switches, and the root sections below                                        |
| `models/*.yaml`              | [Provider connections, credential references, and request settings](models-and-authentication.md#model-file-reference) |
| `agents/*.yaml`              | [Instructions, Model selection, Capabilities, and children](agents-and-subagents.md#agent-file-reference)              |
| `subscription_groups/*.yaml` | [Ordered subscription accounts and selection policy](models-and-authentication.md#account-store-locations)             |
| `projects/*.yaml`            | [Working roots, Device bindings, and creation defaults](environments-and-projects.md#project-file-reference)           |
| `devices/*.yaml`             | [Remote Device connections](environments-and-projects.md#add-device-bindings)                                          |
| `extensions/*.yaml`          | [Harness Plugins, Environment profiles, and Run Extensions](extensions-and-mcp.md)                                     |
| `mcp/*.yaml` or `mcp/*.json` | [External tool servers](mcp.md)                                                                                        |
| `subagents/*.md`             | [Lightweight child instructions](agents-and-subagents.md#write-a-markdown-subagent)                                    |
| `AGENTS.md`                  | [Global guidance](agents-and-subagents.md#instructions-and-guidance)                                                   |

The loader scans immediate files only. It does not recurse, merge ancestor directories, or follow resource symlinks. Extensions are case-sensitive; `subagents/README.md` is excluded. Each file defines one resource, except the MCP multi-server `mcpServers` format.

Supported schema versions retain unknown additive fields with warnings, without applying them. Unsupported versions, duplicate IDs or keys, YAML anchors/aliases, and invalid references reject the candidate configuration.

### Skipped Capabilities

A missing or invalid ordinary Agent Capability is skipped with a warning naming the Agent, Capability, and reason. Valid entries remain usable and the source YAML stays unchanged. These `capability_warnings` appear in the TUI, App status, and `config validate`; warnings alone do not fail validation. Fix the name or arguments, install the trusted implementation, or remove the entry.

Permission policy is stricter. An invalid `ToolPermissionsCapability`, invalid merged shell-review policy, or missing required reviewer Model rejects validation and composition. Disabling the root shortcut does not disable an explicit Agent policy. Invalid explicit defaults are not replaced by broader choices. Runtime provider errors remain runtime errors.

## What wins, and when edits apply

For a new Thread, the first applicable selection wins:

1. Explicit creation or launch choice.
2. Selected Project default.
3. Selected Agent default for Harness Plugins or MCP.
4. Root YAML default.
5. Built-in Environment fallback.

Lists replace rather than merge; `[]` selects none. Existing Threads retain their selected IDs until changed explicitly. Editing a selected resource changes what later Runs resolve, while changing a global default chiefly affects new Threads.

| Change                                                               | Apply it by…                                                                 |
| -------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| Model, Agent, extension, tool switch, or built-in child selection    | Saving a valid tree and starting a new Run                                   |
| Default resource IDs                                                 | Creating a new Thread, or explicitly changing an existing Thread's selection |
| `process`, `envd`, `browser`, listener origins, or MCP Apps listener | Restarting the application                                                   |
| `display`                                                            | Restarting the TUI; `/mode` and `/theme` can change the current presentation |
| Long-text delivery policy                                            | Starting a new root Run; later steering input uses that Run's policy         |
| Interaction timeout                                                  | Opening a new TUI interaction, or admitting a new WebUI Run                  |

The App accepts stable, complete configuration edits asynchronously. Invalid or partially saved trees leave the last accepted version active and produce a diagnostic. Save all related files before validating. Active Runs keep their captured configuration; deleting conversation state is not a reload procedure. At startup, missing Agent or effective reviewer Models are fatal: repair the named reference before restarting.

**TUI selections.** `/agent` changes the Thread Agent. `/model` remembers a per-Project choice; `/model default` clears it. Neither writes YAML. Explicit launch `--agent` and noninteractive calls ignore the remembered Model. TUI resume reapplies current MCP defaults in Project → Agent → global order; other saved resource selections remain. See [Project defaults](environments-and-projects.md#defaults-for-new-conversations).

**Resume.** The Thread Agent resolves against current resources. An explicit Model override in the current TUI remains; a later TUI launch uses the Thread's saved default Model, then the launch Project's remembered Model. History is not used to infer a Model. Saved reasoning is restored without an override only when the continuation used the effective Thread-default or Agent Model. Resume first, then change selections: `--resume` cannot accompany Agent, Environment, or title launch overrides.

**File-backed content.** MCP clients already constructed retain their values; reconstructing an older capture verifies its literal-bearing source and can fail if that source changed. Skill catalog membership is fixed per Run, while Skill files remain readable through the Environment rather than being copied into the capture. See [MCP sources](mcp.md#literal-values-and-environment-references) and [Skill lifetime](skills-and-content-plugins.md#automatic-sources-and-precedence).

## Starter root document

After setup, keep the generated root file and edit only the fields you need. For manual configuration, this small document selects an **existing** `agent-coder` resource:

```yaml title="a13n-harness-ui.yaml — minimal example"
schema_version: "1"
defaults:
  agent: agent-coder
  environment_profile: environment-native
subagents:
  include: [code-reviewer, executor, explorer]
```

Create that Agent and its Model with the [file-based Agent guide](agents-and-subagents.md#create-an-agent-from-files), or substitute your Agent ID. Omitted fields use the defaults below. All later YAML blocks on this page are **sections to merge into this root file**; combine keys with existing sections instead of adding duplicate mappings.

### Default resource selections

| Field under `defaults`       | Default | Purpose                                                                 |
| ---------------------------- | ------- | ----------------------------------------------------------------------- |
| `agent`                      | `null`  | Root Agent; setup selects one                                           |
| `project`                    | `null`  | Optional Project for application callers; TUI uses its launch directory |
| `environment_profile`        | `null`  | Local execution profile; final fallback is `environment-native`         |
| `harness_plugins`            | `[]`    | Ordered, unique Harness Plugin IDs                                      |
| `environment_run_extensions` | `[]`    | Ordered, unique Run Extension IDs                                       |
| `mcp_servers`                | `[]`    | Ordered, unique MCP server IDs                                          |

Setup writes the default Agent and Environment, not a `project-local` resource. A projectless application conversation works in its own `thread-files/tmp/`, with attachments and global guidance/Skills when enabled. The TUI selects a Project from its launch directory on first input or explicit resume. Resuming from another directory reassigns the Project without replacing history or other settings.

The file-only `configuration` mount exposes the selected YAML's directory. It reuses an existing mount of that exact directory and does not establish a shell working directory. The whole directory is accessible, including sensitive files it contains.

### Display settings

| Field under `display`     | Default   | Values or bounds                                     |
| ------------------------- | --------- | ---------------------------------------------------- |
| `theme`                   | `auto`    | `auto`, `dark`, `light`                              |
| `mode`                    | `concise` | `concise`, `detailed`                                |
| `show_status`             | `true`    | Show the status line                                 |
| `max_tool_result_lines`   | `5`       | 1–200 preview lines; compact renderers can use fewer |
| `max_tool_argument_chars` | `8192`    | 128–65536 retained display characters                |

`--display` and `/mode` override the mode. `/theme` lasts until exit. Display controls affect presentation, not reasoning or permissions.

### Built-in tools and subagents

| Root field                                | Default     | Purpose                                                                  |
| ----------------------------------------- | ----------- | ------------------------------------------------------------------------ |
| `tools.enable_ask_user_question`          | `true`      | Include the native question tool                                         |
| `tools.enable_codeact`                    | `true`      | Include CodeAct and `store`/`load`/`forget`                              |
| `tools.interaction_timeout_seconds`       | `120`       | Positive finite seconds per TUI interaction or WebUI root decision batch |
| `tools.execution.mode`                    | `inline`    | `inline` waits; `auto` enables timed handoff for eligible generic tools  |
| `tools.execution.foreground_wait_seconds` | `5.0`       | Nonnegative finite wait; fractional seconds allowed                      |
| `input.steering_mode`                     | `immediate` | `immediate` interrupts waiting; `queued` waits for a safe boundary       |
| `subagents.include`                       | `[]`        | Ordered unique names: `code-reviewer`, `executor`, `explorer`            |

Normal setup includes all three built-in children. Advanced setup offers all or none; edit the list for a subset. Definitions remain package-owned, with no copied Markdown files. Inspect inclusion with `a13n-harness-ui config subagents`. See [child inheritance and conflicts](agents-and-subagents.md#built-in-subagents).

Global disabled tool switches override explicit Agent Capability selections; tool allowlists still apply. Setup fills omitted tool fields and preserves existing values. The old `tools.ask_user_question_timeout_seconds` key is accepted on input; saves use `interaction_timeout_seconds`. Timeout never invents an answer or approves a command, and editing an answer does not restart the timer. See [TUI decisions](everyday-use.md#approvals-and-questions) and [WebUI decisions](webui.md#questions-and-approval-timeouts).

For a shorter foreground wait, merge:

```yaml title="a13n-harness-ui.yaml — tool execution"
tools:
  execution:
    mode: auto
    foreground_wait_seconds: 0.05
```

This waits 50 ms, not a 50 ms execution deadline. Process-capable top-level Shell calls yield a receipt after the wait in **both** modes; fast commands return results directly. Foreground-only Shell uses the generic policy. Immediate steering can release either wait early while the original call continues with its captured settings.

Live `exec-*` Shell executions remain controllable across Runs in the same App. Inside CodeAct, Shell returns native results and Run-local process references; only the outer program hands off. Tool records and bounded completed history remain in App memory. See [execution status and cancellation](webui.md#tool-executions-and-immediate-steering).

### Shell review shortcut

Use the [shell-review recipe](configuration-recipes.md#configure-shell-review) to select and test a reviewer. Root `security.shell_review.enable` defaults to `false`. Enabling it applies review to `environment.shell_exec` in root and child Runs. Explicit shortcut fields override the Agent's shell rule, including `allow`, `deny`, or `ask`; unrelated rules and reviewer instructions remain.

Omitted/null fields inherit the Agent policy, then use these defaults:

| Field under `security.shell_review` | Default after inheritance | Values                                                                  |
| ----------------------------------- | ------------------------- | ----------------------------------------------------------------------- |
| `risk_threshold`                    | `extra_high`              | `low`, `medium`, `high`, `extra_high`; flag risk at or above this level |
| `model`                             | Effective Agent Model     | Saved Model ID                                                          |
| `on_flagged`                        | `approval_required`       | `deny`, `approval_required`                                             |
| `on_error`                          | `allow`                   | `deny`, `approval_required`, `allow` for non-timeout errors             |

Reviewer timeout always denies execution. `allow` continues remaining permission checks; human approval has its own interaction timeout. Invalid merged policies or required Model references reject validation. Disabling the shortcut retains explicit Agent policies. An explicit policy needs one `ToolPermissionsCapability` with nested `review` and a `review` permission on the assessed tools; merely defining a reviewer does not activate it.

**Guardian linking.** `guardian_credits: true` requests provider credit linking; the default is `false`. New setup enables it for Codex subscription connections and disables it for API-key/other connections, including selection of existing Models. Setup and Add Agent preserve existing shell-review settings; Add Model does not change them.

Both Models must use `openai-codex:` or `openai-responses:`. Linking uses the requesting provider response ID, including calls through CodeAct/ToolSearch. Unsupported protocols or missing IDs retain ordinary review and diagnostics. Linking changes neither credentials, endpoint, service tier, policy, nor usage accounting. Provider rejection follows `on_error` without an unlinked retry; timeout denies. API-key Responses can opt in, with eligibility and charges determined by the provider.

### Long-text inputs

`input.long_text_threshold_chars` defaults to `8000`. Longer user-text blocks become retained UTF-8 attachments; the Model receives a path and reading instruction. Set a positive integer to change the threshold, or `null` to keep text inline:

```yaml title="a13n-harness-ui.yaml — keep long text inline"
input:
  long_text_threshold_chars: null
```

The selected Agent needs `view` and a readable `thread-files` mount. Otherwise text stays inline with a notice. Saving failure fails the submission or rejects the added message. Generated files share attachment limits: eight files, 10 MiB each, and 20 MiB per input, plus HTTP request limits.

The policy covers root input and later steering under the captured Run settings. Original text survives restart and scratch cleanup through its attachment handle. Model history retains the reference; tool reads still consume context. Existing history, images, and TUI paste folding are unchanged.

### Goal checks

`max_goal_iterations: 10` permits ten extra checks after the first response for `/goal` or the WebUI Goal toggle. Values at or below zero disable automatic follow-ups. Each new Goal captures its limit; edits do not reset suspended Goals or change active budgets. Request, tool, and token limits also apply. See [Goal behavior and recovery](everyday-use.md#work-toward-a-goal).

### Background compaction

Agents with the `compaction` Capability can summarize earlier history while a root Run continues. Defaults:

```yaml title="a13n-harness-ui.yaml — background compaction"
background_compaction:
  enabled: true
  blocking_threshold: 0.95
```

The ordinary trigger is 90% context occupancy. Background work requires verified spare capacity. At 95% counted input occupancy, the Run waits for existing work or compacts synchronously; output reserves can require an earlier wait. `blocking_threshold` accepts values greater than zero and at most one. Unknown/insufficient capacity selects synchronous work when none is running; background failure also falls back to synchronous work under current usage limits. Children compact synchronously.

A completed summary is saved before use, then applied at a safe model boundary or restored by a later Run with its new input. Idle history is not rewritten merely because a summary finished. Restart restores completed results without replaying interrupted summary requests.

Cleanup runs at startup and hourly. Processed summaries are marked with the saved checkpoint before removal; unused work expires after seven days, protecting active Runs/requests and preserving conversation history. Embedded Apps can change `StorageSettings.compaction_retention_seconds`. Disabling background mode affects later Runs; saved unexpired summaries remain usable and Model budgets stay unchanged.

### File memory

Memory is enabled by default. Beside the selected YAML, `memory/global/` stores cross-Project facts and `memory/projects/<project-id>/` stores Project facts. Projectless conversations use global memory only. Keep `MEMORY.md` as a short index and detailed topics in separate files. Memory is historical context, separate from conversation history and Full Control permissions.

In **Settings → General → Memory**, choose the organizer Model and instructions, or merge:

```yaml title="a13n-harness-ui.yaml — memory organization"
memory:
  enabled: true
  auto_organize:
    enabled: true
    model: model-primary
    instructions: Keep decisions concise and preserve useful source references.
```

Use an existing Model ID. Omitted/null `model` follows `defaults.agent`; a configured Model is required. The organizer receives scoped memory tools, not the Agent's other tools or instructions. It preserves uncertainty and user corrections and reports changes.

WebUI input can trigger organization of changed memory. Each scope has a read-only Memory Thread; organization reads neither old conversations nor other Projects. General settings shows outcomes and usage, which can incur Model charges.

| State                                   | Next attempt                                                            |
| --------------------------------------- | ----------------------------------------------------------------------- |
| Empty, unchanged, busy, or cooling down | No Model request                                                        |
| Success                                 | After one hour, triggered by new input                                  |
| Failure, cancellation, or interruption  | After fifteen minutes, triggered by new input                           |
| Active attempt                          | Up to twelve requests and five minutes; new input does not interrupt it |

Disable `auto_organize.enabled` to keep memory without automatic requests. Disable `memory.enabled` to stop attaching memory to later Runs. Either switch or server shutdown cancels maintenance and retains partial edits. Files remain; active foreground Runs retain their settings.

Back up `memory/` separately from application data. `.a13n-memory/` holds locks and organizer state. Optional Git supplies bounded diff hints, not revision history, and does not modify your repository.

### Media understanding

`media_understanding.image`, `.video`, and `.audio` select saved Model IDs for file `view` when the active Model cannot read that media natively. Each defaults to `null`, retaining the corresponding Harness environment-variable fallback. Configure **Settings → Models** or `/model defaults`; see [selection and input requirements](models-and-authentication.md#media-understanding-defaults).

### Process settings

Restart after changing these fields:

| Field under `process`   | Default     | Purpose                                                                       |
| ----------------------- | ----------- | ----------------------------------------------------------------------------- |
| `pricing_auto_update`   | `true`      | Refresh prices used in later cost estimates                                   |
| `terminal_update_check` | `true`      | Show startup package-update notices; never install automatically              |
| `log_level`             | `INFO`      | `CRITICAL`, `ERROR`, `WARNING`, `INFO`, `DEBUG`; normalized uppercase         |
| `log_format`            | `pretty`    | `pretty` or `json` for noninteractive logs; interactive diagnostics use files |
| `max_object_bytes`      | `268435456` | Uncompressed storage-object limit; 1 KiB–1 GiB                                |

The default object limit is 256 MiB per object, including checkpoints, not a Thread quota or context budget. If a checkpoint is oversized, raise it and restart before continuing. Larger limits increase peak memory. Failed saves keep the previous checkpoint without truncating history; lowering the limit can make larger saved objects unreadable.

Official Model facts and Harness pricing supplements have a separate background refresh from GitHub `main`. Set `A13N_OFFICIAL_MODELS_AUTO_UPDATE=0` before startup to disable it. Failed downloads retain valid/bundled data and retry with backoff and jitter. Refresh does not rewrite Models or active Runs; PDF input remains opt-in. Offline use requires disabling both this refresh and `pricing_auto_update`. `--no-update-check` disables the package check for one invocation. See [logs and updates](automation-and-troubleshooting.md#logs-updates-and-exit).

### Local envd runtime

Configure **Settings → Environments → Local runtime**, or merge:

```yaml title="a13n-harness-ui.yaml — local runtime"
envd:
  executable: null
  computer_use: false
  monitoring_interval_seconds: 60
```

Restart to apply. Saving does not download, launch, or request OS permissions. `executable: null` selects the managed release required by the installed client. A custom/source build needs an absolute path named `a13n-envd` (`a13n-envd.exe` on Windows); `~` expands on the Host. Custom binaries are not downloaded. Omitting `envd` retains an embedding application's executable setting and leaves desktop access off.

`computer_use` permits desktop-capable local runtimes; the Agent also needs `environment-desktop`. `monitoring_interval_seconds` accepts 10–60 seconds and defaults to 60 for App collection and built-in local Sandbox/Envd/Desktop sampling. External daemons and custom launch recipes keep their own intervals. Browser refresh is one minute; history retains one hour. See [runtime management](environments-and-projects.md#manage-local-envd).

### Host browser

`browser.mode` defaults to `auto`: WebUI prepares the shared browser at startup and automatically provides tools to eligible Full Control Runs. Conversation pages open when the Agent browses or you choose **Open browser**. Set the mode to `disabled` to opt out. Save and restart Harness UI after changing the mode.

Leave `browser.executable` and `browser.vnc_executable` at their default `null` values to select compatible installed programs automatically at startup. Automatic selection needs no configuration save or restart. Harness UI detects programs; you install any missing Host components.

To choose specific programs, use **Settings → General → Host browser** or merge this example into the root YAML. Replace the absolute paths with your installed programs. `vnc_executable` applies only to Linux:

```yaml title="a13n-harness-ui.yaml — Host browser"
browser:
  executable: /usr/bin/chromium
  vnc_executable: /usr/bin/Xtigervnc
```

Save changed overrides and restart Harness UI. Choose **Automatic** to clear an override. An invalid explicit path blocks startup rather than falling back to another program.

Browser automation requires a Linux or macOS WebUI Host, computer sharing, and a Full Control Run with Auto mode. On macOS, install Chromium for automation. Optional read-only preview needs macOS 14 or later and Screen Recording permission for the application running Harness UI. It shows only the conversation's owned browser window. **Open Mac Screen Recording settings** opens the Host settings; you grant permission there. Missing preview permission leaves automation available. macOS uses no VNC password or remote input.

All Projects share one browser instance/profile at `<data_root>/browsers/profile`; there is no separate profile-path setting. Follow [first-time browser setup](webui.md#first-time-setup) for Host preparation and a first navigation. Keep Chromium's sandbox enabled; the [explicit sandbox option](command-reference.md#webui) is available for deployments that require disabling it.

### WebUI allowed origins

`webui.allowed_origins: []` adds no public addresses beyond the listener's bind-address and loopback rules. To admit a reverse-proxy address, add its exact HTTP(S) origin and restart:

```yaml title="a13n-harness-ui.yaml — public request address"
webui:
  allowed_origins:
    - https://agents.example.com
```

Non-default ports are significant. Default ports and a trailing `/` are normalized. Credentials, paths, query strings, fragments, and partial wildcards are invalid. Literal `"*"` admits any request address and removes Host/DNS-rebinding protection; prefer exact origins.

This controls request-address admission, not CORS. Authentication and same-origin checks remain; listed origins cannot call each other's APIs. A configuration reload does not change an active listener. See [reverse proxies](webui.md#reverse-proxies-and-public-addresses).

### WebUI Sidekick

**Settings → General → Sidekick** controls defaults for independent work. Omitted settings or `sidekick: {}` enable it; `sidekick: null` disables it. To choose a default Model for new Sidekick Threads:

```yaml title="a13n-harness-ui.yaml — Sidekick defaults"
webui:
  sidekick:
    agent: null
    model: model-worker
```

Use saved IDs. Null/omitted `agent` inherits the caller; null/omitted `model` follows that Agent's Model. A configured Model becomes the new Thread's saved default for follow-ups. Explicit `create_thread(model_id=...)` or a Run picker overrides only that Run.

Save to affect new WebUI root Runs. Saving starts no work, changes no existing Thread, and does not change `defaults.agent`, TUI Runs, or delegated children. Setup preserves disabled/custom choices. Disabling removes Sidekick instructions; discovery tools remain independently available under role scope. See [collaboration and delivery](webui.md#agent-collaboration-and-sidekick).

### MCP lifetime and protocol

Root `mcp.host_owned_servers` defaults to `[]`: selected server clients can outlive logical Runs in CLI/WebUI, but the list does not select Agent tools. `mcp.protocol_overrides` defaults to `{}` and maps existing IDs to `auto`, `legacy`, or `2026-07-28`; omitted IDs use SDK negotiation. Later Runs capture changes. Live clients and human answers are runtime state. See [MCP connection lifetime](mcp.md#connection-lifetime-and-protocol) and [human input](mcp.md#human-input-from-mcp-servers).

### WebUI MCP Apps

Enable interactive results with `webui.mcp_apps.enabled: true` and server IDs in `webui.mcp_apps.servers`. WebUI adds these servers to root and child Agents alongside generic MCP selections, deduplicating without rewriting Agent files or Thread selections. CLI remains generic-only. Restart when enabling Apps or changing its separate-origin listener. Use the [MCP Apps guide](mcp-apps.md) for complete local/proxy setup and interaction permissions.

## Data root and environment variables

The data root stores conversations, TUI Model preferences, captures, logs, API keys, and Content Plugins. Selection order:

1. `--data-root PATH`.
2. `A13N_HARNESS_UI_DATA_ROOT`.
3. `<configuration-directory>/data`.

Changing this location opens separate state; it does not migrate conversations. Relative bootstrap paths resolve from the launch directory. Project roots must be absolute after `~` expansion; a saved missing root must become available before a Run uses it.

| Variable or reference                    | Purpose                                                    |
| ---------------------------------------- | ---------------------------------------------------------- |
| `authentication.env`                     | Read a Model key from the Harness UI process environment   |
| MCP `environment` / `headers` references | Resolve server environment or request values               |
| `CODEX_HOME`                             | One-time Codex migration source; default `~/.codex`        |
| `COPILOT_HOME`                           | Copilot CLI account reuse; default `~/.copilot`            |
| `GROK_AUTH_PATH`, `GROK_HOME`            | One-time Grok migration source                             |
| `GROK_AUTH`                              | Inline Grok credentials are not imported; use native login |
| `COLORFGBG`                              | Passive terminal metadata for automatic theme selection    |

There is no general `A13N_HARNESS_UI_*` override mechanism. `storage`, `envd_runtime`, and shutdown timeouts belong to embedding/runtime settings, not root YAML. Listener/authentication options are [CLI arguments](webui.md).

## Outbound HTTP proxies

Set proxy variables in the environment that launches Harness UI, then restart. For Bash:

```bash
export http_proxy=http://127.0.0.1:8888
export https_proxy=http://127.0.0.1:8888
export no_proxy=localhost,127.0.0.1,::1
```

Uppercase forms and `ALL_PROXY` work; selection/bypass follow `httpx2`. Host Web requests, remote HTTPS MCP, updates, and [Model HTTP clients](../a13n-harness/models.md#outbound-http-proxies) honor them. A container needs a proxy address reachable from inside that container. Proxy failures do not retry directly.

The proxy owns destination DNS/network restrictions. Host Web checks URLs, redirects, deadlines, and response bounds without pre-resolving/pinning destination IPs, including direct and `NO_PROXY` requests. TLS verification is on; only set `A13N_OUTBOUND_TLS_VERIFY=false` when intentionally accepting unverified certificates. See [TLS scope and risks](../a13n-harness/models.md#outbound-tls-verification).

Plaintext loopback MCP and plaintext local/provider-private Envd attachments stay direct. HTTPS Envd attachments honor proxies. Third-party SDK transports retain their own behavior; daemon pairing/reverse WebSockets are separate from the Python attachment client.

## Run configuration

Use root `run_configuration` to restrict destinations for a root Run and its children. Include every required Model, Web, and MCP host:

```yaml title="a13n-harness-ui.yaml — Run host rules"
run_configuration:
  allowed_hosts:
    - api.example.com
    - 'regex:(?:[a-z0-9-]+\.)*docs\.example\.com'
  extensions:
    example.reader: {images: true}
```

Omitted/null `allowed_hosts` allows all destinations; `[]` denies all. Rules match normalized hostnames/IPs exactly, or use `regex:` for a full-hostname Python match. This pattern allows `docs.example.com` and its subdomains, not `docs.example.com.evil.test`. Single-quoted YAML preserves backslashes. Empty/invalid expressions fail validation; URLs, paths, ports, globs, and CIDRs are not host patterns.

API-key Model clients and Host Web/MCP check declared destinations and redirect hops without DNS resolution or IP pinning. Restrictive rules reject opaque subscription transports. The captured policy stays fixed for root and child Runs. Namespaced `extensions` need an opting-in consumer; they are not Capability arguments. Shell/plugin networking needs Environment or deployment isolation. See [host rules](../a13n-harness/context.md#host-rules-and-regular-expressions).
