# Configuration reference

Harness UI uses ordinary YAML and Markdown files. **The root file controls application defaults; Model and Agent files control agent behavior.** Start with `a13n-harness-ui setup`, then edit those files when you need more control. Do not edit the local database to change configuration.

For task-oriented examples, start with [common configuration recipes](configuration-recipes.md). This page is the root-file and loading reference.

## Find the right setting

| I want to configure…                                      | Open…                               | Reference                                                                        |
| --------------------------------------------------------- | ----------------------------------- | -------------------------------------------------------------------------------- |
| Startup checks and logging                                | `a13n-harness-ui.yaml` → `process`  | [Process settings](#process-settings)                                            |
| Default Agent, Environment, plugins, or MCP               | `a13n-harness-ui.yaml` → `defaults` | [Default selections](#default-resource-selections)                               |
| Theme and output detail                                   | `a13n-harness-ui.yaml` → `display`  | [Display settings](#display-settings)                                            |
| Questions, CodeAct, built-in children                     | Root `tools` and `subagents`        | [Built-in tools](#built-in-tools-and-subagents)                                  |
| Provider, API key reference, endpoint, reasoning, context | `models/*.yaml`                     | [Model fields](models-and-authentication.md#model-file-reference)                |
| Instructions, Capabilities, visible tools, children       | `agents/*.yaml`                     | [Agent fields](agents-and-subagents.md#agent-file-reference)                     |
| Extra workspace roots                                     | `projects/*.yaml`                   | [Project fields](environments-and-projects.md#project-file-reference)            |
| External tools                                            | `mcp/*.yaml` or `mcp/*.json`        | [MCP fields](extensions-and-mcp.md#mcp-field-reference)                          |
| Installed integrations                                    | `extensions/*.yaml`                 | [Extension fields](extensions-and-mcp.md#harness-plugin-and-run-extension-files) |

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

| Path                       | Purpose                                               | Detailed reference                                                    |
| -------------------------- | ----------------------------------------------------- | --------------------------------------------------------------------- |
| `AGENTS.md`                | Optional global contextual guidance                   | [Guidance](agents-and-subagents.md#instructions-and-guidance)         |
| `models/*.yaml`            | Reusable Models and credential references             | [Models](models-and-authentication.md#model-file-reference)           |
| `agents/*.yaml`            | Agent definitions and child rosters                   | [Create an Agent](agents-and-subagents.md#create-an-agent-from-files) |
| `subagents/*.md`           | Your lightweight child instructions                   | [Markdown children](agents-and-subagents.md#write-a-markdown-child)   |
| `projects/*.yaml`          | Named ordered workspace roots                         | [Projects](environments-and-projects.md#project-file-reference)       |
| `extensions/*.yaml`        | Harness Plugins, Environment profiles, Run Extensions | [Extensions](extensions-and-mcp.md)                                   |
| `mcp/*.yaml`, `mcp/*.json` | MCP server definitions                                | [MCP](extensions-and-mcp.md#mcp-servers)                              |

Only immediate lowercase `.yaml` or `.md` files are scanned, plus `.json` in `mcp/`; `subagents/README.md` is ignored. Filenames are for people; the resource `id` owns references. There is no recursive scan, YAML include, ancestor configuration merge, or symlink-based resource discovery. One file defines one resource, except MCP files may contain a multi-server `mcpServers` object. MCP environment/header values accept literals and environment references; see [MCP configuration](extensions-and-mcp.md#mcp-servers). Unknown fields, unsupported schema versions, duplicate IDs/keys, aliases, anchors, and invalid references reject the candidate configuration.

### Skipped Capabilities

A missing, ambiguous, unloadable, or invalid Capability in an Agent produces a warning instead of blocking conversations. Harness UI skips only that entry, keeps valid entries (including other `NativeTool` entries), and leaves your YAML unchanged. The warning names the Agent ID, Capability key, and reason. The interactive CLI displays these warnings; `config validate` and the Web API's App status expose them as `capability_warnings`. Validation still succeeds when these are the only problems.

Correct the Capability name or arguments, install its trusted implementation if needed, or remove the entry. If an explicitly configured default Capability is invalid, it stays skipped rather than being replaced with broader defaults. A missing Shell Review auxiliary Model also skips that review Capability; Environment permissions, mandatory invocation policy, and tool switches still apply.

Only valid selections are captured for a new Run. Already captured Runs do not change, and runtime/model-provider failures are not converted into configuration warnings. Invalid YAML structure, Model resources, and Environment or Plugin configuration still require repair.

## Complete root document

This example shows every root option. Replace the example default IDs with resources you actually created, or leave those selections `null`.

```yaml
schema_version: "1"
process:
  pricing_auto_update: true
  terminal_update_check: true
  log_level: INFO
  log_format: pretty
input:
  long_text_threshold_chars: 8000
defaults:
  project: null
  agent: null
  environment_profile: environment-native
  harness_plugins: []
  environment_run_extensions: []
  mcp_servers: []
display:
  theme: auto
  mode: concise
  show_status: true
  max_tool_result_lines: 5
  max_tool_argument_chars: 8192
tools:
  enable_ask_user_question: true
  ask_user_question_timeout_seconds: 120
  enable_codeact: true
subagents:
  include: []
```

### Process settings

These settings take effect when the application starts; restart after changing them.

| Field                           | Default  | Meaning                                                                                  |
| ------------------------------- | -------- | ---------------------------------------------------------------------------------------- |
| `process.pricing_auto_update`   | `true`   | Update the App-owned upstream pricing catalog; not an authorization to buy credits       |
| `process.terminal_update_check` | `true`   | Check for a package update at terminal startup; installation still requires confirmation |
| `process.log_level`             | `INFO`   | `CRITICAL`, `ERROR`, `WARNING`, `INFO`, or `DEBUG`; normalized uppercase                 |
| `process.log_format`            | `pretty` | Noninteractive logging: `pretty` or `json`; interactive diagnostics use files            |

Use `--no-update-check` for a one-invocation override. See [updates and logs](automation-and-troubleshooting.md#logs-updates-and-exit).

### Long-text inputs

`input.long_text_threshold_chars` defaults to `8000`. A user-text block longer than that many characters is automatically saved as a retained UTF-8 file. The model receives its file path and a reading instruction, **not an inline preview or summary**. Short text is unchanged. Use a positive integer to change the threshold, or `null` to keep all text inline:

```yaml
input:
  long_text_threshold_chars: null
```

The policy applies to normal root messages and messages added while a root Run is active. Each Run captures its configuration; changes affect later Runs. Terminal paste folding is independent and still expands the authored text before submission.

The selected Agent must have the built-in `view` tool enabled and a readable `thread-files` mount. Without that access, the original text stays inline with a notice; no tools are enabled automatically. A file-save failure fails the submission's execution or rejects the added message. Generated input files count toward the existing eight attachments, 10 MiB per file, and 20 MiB per input limits; HTTP request limits still apply.

Original text remains available through the Thread attachment handle after restart and scratch cleanup. Model history keeps the reference, not an automatic expansion of the file. Reading content through a tool still consumes context, especially for tasks requiring the whole document. Existing history and images are not converted by this setting.

### Default resource selections

| Field                                 | Default | Meaning                                                                                               |
| ------------------------------------- | ------- | ----------------------------------------------------------------------------------------------------- |
| `defaults.project`                    | `null`  | Optional Project for application callers; omit for no project. The terminal uses its launch directory |
| `defaults.agent`                      | `null`  | Default root Agent; setup sets this to its chosen Agent                                               |
| `defaults.environment_profile`        | `null`  | Environment profile; absent selection ultimately uses `environment-native`                            |
| `defaults.harness_plugins`            | `[]`    | Ordered exact Harness Plugin IDs                                                                      |
| `defaults.environment_run_extensions` | `[]`    | Ordered exact Environment Run Extension IDs                                                           |
| `defaults.mcp_servers`                | `[]`    | Ordered exact MCP server IDs                                                                          |

Lists must be unique. Global defaults initialize new sessions; they do not silently rewrite existing sessions' sticky resource selections. Agent-level MCP and Harness Plugin selections override their corresponding defaults. A selected resource's valid file edits can still change its behavior on later Runs.

Normal setup writes only the default Agent and Environment profile, not a `project-local` resource or `defaults.project`:

```yaml
defaults:
  agent: agent-api-key
  environment_profile: environment-native
```

An application-created conversation without a Project uses its own `thread-files/tmp/` working directory. It still has attachments, global Skills when enabled, and global guidance. The terminal selects a Project from its launch directory on the first prompt or explicit resume. Resuming an existing conversation from another directory assigns it to the launch directory's Project without changing its history or other settings.

The Agent can read and write the selected configuration directory through the file-only `configuration` mount. This defaults to `~/.a13n-harness-ui`; with `--config`, it is the chosen YAML file's parent directory. It is not a project workspace and does not grant shell execution through that mount. If an existing working mount already exposes the exact directory, its route is reused. Resource edits are validated before acceptance and affect later Runs; invalid edits leave the last accepted configuration active. Process settings require restart. The mount exposes the whole selected directory, so keep sensitive file contents out of messages and logs.

### Display settings

| Field                             | Default   | Allowed values / meaning                                                          |
| --------------------------------- | --------- | --------------------------------------------------------------------------------- |
| `display.theme`                   | `auto`    | `auto`, `dark`, `light`                                                           |
| `display.mode`                    | `concise` | `concise`, `detailed`                                                             |
| `display.show_status`             | `true`    | Show the status line                                                              |
| `display.max_tool_result_lines`   | `5`       | Result-preview budget, 1–200 lines; compact tool-specific rendering may use fewer |
| `display.max_tool_argument_chars` | `8192`    | Retained argument-display budget, 128–65536 characters                            |

Display defaults are read at startup. `--display` and live `/mode` override the configured mode. `/theme` changes the theme for the current session. These options affect presentation, not model reasoning or permissions.

### Built-in tools and subagents

| Field                                     | Default | Meaning                                                                                             |
| ----------------------------------------- | ------- | --------------------------------------------------------------------------------------------------- |
| `tools.enable_ask_user_question`          | `true`  | Include native `ask_user_question` in newly resolved Runs                                           |
| `tools.ask_user_question_timeout_seconds` | `120`   | Positive finite seconds for each displayed terminal question, not shell approval or model execution |
| `tools.enable_codeact`                    | `true`  | Include native CodeAct runners and explicit `store`/`load`/`forget` state tools                     |
| `subagents.include`                       | `[]`    | Ordered named built-ins: `code-reviewer`, `executor`, `explorer`                                    |

Setup writes all three `tools` fields explicitly into the selected root YAML (by default `~/.a13n-harness-ui/a13n-harness-ui.yaml`), filling omitted fields with these defaults and preserving existing values.

Global disabled tool switches take precedence over explicit Agent capability selections. Tool allowlists still apply. The terminal question timeout does not choose an answer or approve a command; [decision handling](everyday-use.md#approvals-and-questions) explains recovery.

For all built-ins use `[code-reviewer, executor, explorer]`; for a subset use, for example, `[explorer]`. Setup only asks all or none. Definitions remain package-owned; inclusion does not write `subagents/*.md`. [Built-in subagents](agents-and-subagents.md#built-in-subagents) explains inheritance and name conflicts.

## What wins, and when edits apply

- A live `/agent` selection applies to subsequent operations without writing YAML. `/model` temporarily overrides only the model in the current TUI session; `/thinking` overrides its reasoning setting.
- Launch options such as `--agent` and `--environment-mode` select a new session's values.
- Otherwise accepted Agent resources and root defaults apply, then documented package defaults.
- Resume restores the Thread's selected Agent against current resources, preserving an explicit Model override in the current TUI session. A later TUI invocation does not restore a previous temporary Model choice. Without a Model override, saved reasoning is restored only when the continuation used that Agent's configured Model. Resume launch options cannot be combined with Agent, Environment, or title overrides.
- A complete valid file tree becomes the next accepted generation. Invalid or partial saves leave the preceding accepted generation active and produce diagnostics. Fix the file and run `config validate` again.
- A newly admitted Run captures its full composition. File edits, inclusion changes, or package prompt updates never rewrite an active or already captured Run.

This makes multi-file editing practical: write the files, validate the entire tree, and start the next Run only after validation succeeds. `config show` reports accepted configuration; it need not reflect a currently invalid on-disk edit.

## Data root and environment variables

The data root owns local sessions, immutable captures, logs, stored API keys, and installed Content Plugins. Selection order is:

1. `--data-root PATH`.
2. `A13N_HARNESS_UI_DATA_ROOT`.
3. `<configuration-directory>/data`.

Changing it opens separate state; it does not migrate old sessions. Relative bootstrap paths resolve from the launch directory. Project roots must be absolute after `~` expansion and refer to existing directories.

| Input                                                       | Purpose                                                                                    |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `A13N_HARNESS_UI_DATA_ROOT`                                 | Select local data storage                                                                  |
| Variables named by `authentication.env`                     | Model API keys read from the Harness UI process                                            |
| Variables named by MCP `environment` / `headers` references | MCP environment and request values                                                         |
| `CODEX_HOME`                                                | Compatible Codex store location; default `~/.codex`                                        |
| `GROK_AUTH_PATH`, `GROK_HOME`                               | Compatible Grok file-store location                                                        |
| `GROK_AUTH`                                                 | Recognized inline Grok mode, unsupported for shared writable login; switch to a file store |
| `COLORFGBG`                                                 | Passive terminal metadata for automatic theme selection                                    |

There is no general `A13N_HARNESS_UI_*` setting override mechanism. `storage`, `envd_runtime`, and application shutdown timeouts are embedding/runtime settings, **not** root YAML sections. Web listener and authentication options are [process-local CLI arguments](automation-and-troubleshooting.md#browser-ui), not resource configuration.
