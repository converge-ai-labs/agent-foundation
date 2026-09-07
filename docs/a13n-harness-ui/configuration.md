# Configuration reference

Harness UI uses ordinary YAML and Markdown files. Start with `a13n-harness-ui setup`, add more agents with `a13n-harness-ui add agent`, then edit the resulting files when you need more control. There is no generated configuration database to edit and no generic CLI resource-creation command.

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

| Path                | Purpose                                               | Detailed reference                                                    |
| ------------------- | ----------------------------------------------------- | --------------------------------------------------------------------- |
| `AGENTS.md`         | Optional global contextual guidance                   | [Guidance](agents-and-subagents.md#instructions-and-guidance)         |
| `models/*.yaml`     | Reusable Models and credential references             | [Models](models-and-authentication.md#model-file-reference)           |
| `agents/*.yaml`     | Agent definitions and child rosters                   | [Create an Agent](agents-and-subagents.md#create-an-agent-from-files) |
| `subagents/*.md`    | Your lightweight child instructions                   | [Markdown children](agents-and-subagents.md#write-a-markdown-child)   |
| `projects/*.yaml`   | Named ordered workspace roots                         | [Projects](environments-and-projects.md#project-file-reference)       |
| `extensions/*.yaml` | Harness Plugins, Environment profiles, Run Extensions | [Extensions](extensions-and-mcp.md)                                   |
| `mcp/*.yaml`        | MCP server definitions                                | [MCP](extensions-and-mcp.md#mcp-servers)                              |

Only immediate lowercase `.yaml` or `.md` files are scanned; `subagents/README.md` is ignored. Filenames are for people; the resource `id` owns references. There is no recursive scan, YAML include, ancestor configuration merge, or symlink-based resource discovery. One YAML file defines one resource. Unknown fields, unsupported schema versions, duplicate IDs/keys, aliases, anchors, and invalid references reject the candidate configuration.

## Complete root document

This example shows every root option. Replace the example default IDs with resources you actually created, or leave those selections `null`.

```yaml
schema_version: "2"
process:
  pricing_auto_update: true
  terminal_update_check: true
  log_level: INFO
  log_format: pretty
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
  enable_user_input: true
  user_input_timeout_seconds: 120
  enable_codeact: false
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

### Default resource selections

| Field                                 | Default | Meaning                                                                                        |
| ------------------------------------- | ------- | ---------------------------------------------------------------------------------------------- |
| `defaults.project`                    | `null`  | Default Project for application callers; terminal workspace matching uses the launch directory |
| `defaults.agent`                      | `null`  | Default root Agent; setup sets this to its chosen Agent                                        |
| `defaults.environment_profile`        | `null`  | Environment profile; absent selection ultimately uses `environment-native`                     |
| `defaults.harness_plugins`            | `[]`    | Ordered exact Harness Plugin IDs                                                               |
| `defaults.environment_run_extensions` | `[]`    | Ordered exact Environment Run Extension IDs                                                    |
| `defaults.mcp_servers`                | `[]`    | Ordered exact MCP server IDs                                                                   |

Lists must be unique. Global defaults initialize new sessions; they do not silently rewrite existing sessions' sticky resource selections. Agent-level MCP and Harness Plugin selections override their corresponding defaults. A selected resource's valid file edits can still change its behavior on later Runs.

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

| Field                              | Default | Meaning                                                                                             |
| ---------------------------------- | ------- | --------------------------------------------------------------------------------------------------- |
| `tools.enable_user_input`          | `true`  | Include native `ask_user_question` in newly resolved Runs                                           |
| `tools.user_input_timeout_seconds` | `120`   | Positive finite seconds for each displayed terminal question, not shell approval or model execution |
| `tools.enable_codeact`             | `false` | Include native CodeAct runners and explicit `store`/`load`/`forget` state tools                     |
| `subagents.include`                | `[]`    | Ordered named built-ins: `code-reviewer`, `executor`, `explorer`                                    |

Global disabled tool switches take precedence over explicit Agent capability selections. Tool allowlists still apply. The terminal question timeout does not choose an answer or approve a command; [decision handling](everyday-use.md#approvals-and-questions) explains recovery.

For all built-ins use `[code-reviewer, executor, explorer]`; for a subset use, for example, `[explorer]`. Setup only asks all or none. Definitions remain package-owned; inclusion does not write `subagents/*.md`. [Built-in subagents](agents-and-subagents.md#built-in-subagents) explains inheritance and name conflicts.

## What wins, and when edits apply

- A live `/agent` (also `/model`) or `/thinking` choice applies to subsequent operations without writing YAML.
- Launch options such as `--agent` and `--environment-mode` select a new session's values.
- Otherwise accepted Agent resources and root defaults apply, then documented package defaults.
- Resume restores the selected continuation's Model and reasoning against current resources. It does not invent a replacement for a deleted Model. Resume cannot be combined with Agent, Environment, or title overrides.
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
