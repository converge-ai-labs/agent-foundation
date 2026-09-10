# Command reference

This is the complete registered terminal command surface. Shell commands and in-chat slash commands are different parsers; an SDK method or HTTP endpoint does not automatically become a CLI command.

## Shell invocation

```console
a13n-harness-ui [GLOBAL OPTIONS] [COMMAND] [COMMAND OPTIONS]
```

Omitting `COMMAND` opens the interactive terminal. Put global options before the command; use `-h` or `--help` at each level. `--version` is an eager information option. Help and version do not initialize the App, database, Model, or Environment.

Root defaults and startup behavior are described in [Configuration](configuration.md); the tables below describe parser defaults, not resolved configuration or successful provider authentication. `--resume` cannot be combined with new-session Agent, Environment, or title overrides.

### Global options

| Parameter               | Type / choices        | Parser default     | Meaning                                                                                    |
| ----------------------- | --------------------- | ------------------ | ------------------------------------------------------------------------------------------ |
| `--config`              | path                  | `"Sentinel.UNSET"` | Explicit Harness UI configuration YAML (default: ~/.a13n-harness-ui/a13n-harness-ui.yaml). |
| `--data-root`           | path                  | `"Sentinel.UNSET"` | Override the local Harness UI data root.                                                   |
| `--resume`              | text                  | `"Sentinel.UNSET"` | Resume a saved session by ID.                                                              |
| `--agent`               | text                  | `"Sentinel.UNSET"` | Select a configured Agent for this session.                                                |
| `--environment-mode`    | full-control, sandbox | `"Sentinel.UNSET"` | Override the built-in Environment mode for this session.                                   |
| `--environment-profile` | text                  | `"Sentinel.UNSET"` | Override the custom Environment profile for this session.                                  |
| `--display`             | concise, detailed     | `not set`          | Presentation only; overrides display.mode (default concise). /mode switches live.          |
| `--no-update-check`     | boolean               | `false`            | Skip startup update detection for this invocation.                                         |
| `--version`             | boolean               | `false`            | Show the version and exit.                                                                 |

## Commands

The following command paths are literal shell subcommands. Positional arguments follow the path. Authentication commands can mutate account/key stores; plugin installation writes local content; uninstall deletes it. Configuration inspection opens local application state and is not a side-effect-free YAML parser.

### `webui`

Run one foreground WebUI server with bundled browser assets.

| Parameter                                                       | Type / choices   | Parser default     | Meaning                                                                           |
| --------------------------------------------------------------- | ---------------- | ------------------ | --------------------------------------------------------------------------------- |
| `--host`                                                        | text             | `"127.0.0.1"`      | Listener IPv4 or IPv6 address.                                                    |
| `--port`                                                        | integer 1..65535 | `8765`             |                                                                                   |
| `--apikey, --api-key`                                           | text             | `"Sentinel.UNSET"` | Listener API key; overrides A13N_HARNESS_UI_API_KEY (visible in shell arguments). |
| `--dangerous-skip-permissions, --dangerously-bypass-permission` | boolean          | `false`            | Disable Web authentication only; does not change Agent permissions.               |

### `update`

Update this uv-tool installation now, without starting chat or setup.

No command-specific parameters.

### `setup`

Configure a model, context budget, and execution permissions interactively.

| Parameter    | Type / choices | Parser default | Meaning                                                                    |
| ------------ | -------------- | -------------- | -------------------------------------------------------------------------- |
| `--advanced` | boolean        | `false`        | Also choose context, reasoning, shell review, subagents, and instructions. |

### `add agent`

Create another agent without changing existing agents or defaults.

| Parameter    | Type / choices | Parser default | Meaning                                                   |
| ------------ | -------------- | -------------- | --------------------------------------------------------- |
| `--advanced` | boolean        | `false`        | Also customize reasoning, shell review, and instructions. |

### `add model`

Create a reusable model without creating or changing an agent.

| Parameter    | Type / choices | Parser default | Meaning                                                     |
| ------------ | -------------- | -------------- | ----------------------------------------------------------- |
| `--advanced` | boolean        | `false`        | Also customize subscription context and reasoning settings. |

### `run`

Execute one prompt without an interactive terminal.

| Parameter               | Type / choices        | Parser default     | Meaning                                            |
| ----------------------- | --------------------- | ------------------ | -------------------------------------------------- |
| `PROMPT`                | text                  | `required`         | Required positional argument                       |
| `--resume`              | text                  | `"Sentinel.UNSET"` | Continue a saved session.                          |
| `--agent`               | text                  | `"Sentinel.UNSET"` | Agent used for a new session.                      |
| `--environment-mode`    | full-control, sandbox | `"Sentinel.UNSET"` | Built-in execution mode used for a new session.    |
| `--environment-profile` | text                  | `"Sentinel.UNSET"` | Custom Environment profile used for a new session. |
| `--title`               | text                  | `"Sentinel.UNSET"` | Title used for a new session.                      |
| `--format`              | text, json            | `"text"`           |                                                    |

### `config path`

Show the selected configuration and data paths.

| Parameter  | Type / choices | Parser default | Meaning |
| ---------- | -------------- | -------------- | ------- |
| `--format` | text, json     | `"text"`       |         |

### `config validate`

Validate the selected source tree.

| Parameter  | Type / choices | Parser default | Meaning |
| ---------- | -------------- | -------------- | ------- |
| `--format` | text, json     | `"text"`       |         |

### `config show`

Show the accepted configuration.

| Parameter  | Type / choices | Parser default | Meaning |
| ---------- | -------------- | -------------- | ------- |
| `--format` | text, json     | `"text"`       |         |

### `config subagents`

List package-owned subagents and their current inclusion.

| Parameter  | Type / choices | Parser default | Meaning |
| ---------- | -------------- | -------------- | ------- |
| `--format` | text, json     | `"text"`       |         |

### `import subagents`

Preview or apply external subagent imports.

| Parameter        | Type / choices             | Parser default     | Meaning                                                     |
| ---------------- | -------------------------- | ------------------ | ----------------------------------------------------------- |
| `--product`      | claude-code, cursor, codex | `required`         |                                                             |
| `--scope`        | user, project              | `required`         |                                                             |
| `--project-root` | path                       | `"Sentinel.UNSET"` |                                                             |
| `--user-home`    | path                       | `"Sentinel.UNSET"` |                                                             |
| `--apply`        | boolean                    | `false`            | Apply every ready candidate; omission is a dry-run preview. |
| `--format`       | text, json                 | `"text"`           |                                                             |

### `plugin install`

Install one Content Plugin from a Git repository.

| Parameter    | Type / choices | Parser default     | Meaning                                         |
| ------------ | -------------- | ------------------ | ----------------------------------------------- |
| `REPOSITORY` | text           | `required`         | Required positional argument                    |
| `--plugin`   | text           | `"Sentinel.UNSET"` | Plugin ID when the repository contains several. |
| `--ref`      | text           | `"Sentinel.UNSET"` | Git branch, tag, or commit to install.          |
| `--format`   | text, json     | `"text"`           |                                                 |

### `plugin list`

List installed Content Plugins and their directories.

| Parameter  | Type / choices | Parser default | Meaning |
| ---------- | -------------- | -------------- | ------- |
| `--format` | text, json     | `"text"`       |         |

### `plugin uninstall`

Permanently delete the installed Content Plugin directory, including local edits, without another confirmation. Back up edits first; reinstall does not restore them.

| Parameter   | Type / choices | Parser default | Meaning                      |
| ----------- | -------------- | -------------- | ---------------------------- |
| `PLUGIN_ID` | text           | `required`     | Required positional argument |
| `--format`  | text, json     | `"text"`       |                              |

### `environment list`

List Environment modes and profiles.

| Parameter  | Type / choices | Parser default | Meaning |
| ---------- | -------------- | -------------- | ------- |
| `--format` | text, json     | `"text"`       |         |

### `doctor`

Inspect App and extension health.

| Parameter  | Type / choices | Parser default | Meaning |
| ---------- | -------------- | -------------- | ------- |
| `--format` | text, json     | `"text"`       |         |

### `auth status`

Show Model authentication status.

| Parameter  | Type / choices | Parser default | Meaning                      |
| ---------- | -------------- | -------------- | ---------------------------- |
| `PROVIDER` | codex, grok    | `not set`      | Optional positional argument |
| `--format` | text, json     | `"text"`       |                              |

### `auth key list`

List saved key references without returning their key bytes.

| Parameter  | Type / choices | Parser default | Meaning |
| ---------- | -------------- | -------------- | ------- |
| `--format` | text, json     | `"text"`       |         |

### `auth key set`

Add or replace a key using a hidden prompt, never a command-line key.

| Parameter   | Type / choices | Parser default | Meaning                      |
| ----------- | -------------- | -------------- | ---------------------------- |
| `REFERENCE` | text           | `required`     | Required positional argument |

### `auth key delete`

Delete a saved key after confirmation. Future Model resolution using it can fail.

| Parameter   | Type / choices | Parser default | Meaning                               |
| ----------- | -------------- | -------------- | ------------------------------------- |
| `REFERENCE` | text           | `required`     | Required positional argument          |
| `--yes`     | boolean        | `false`        | Confirm the action without prompting. |

### `auth logout`

Remove locally stored Model credentials.

| Parameter  | Type / choices | Parser default | Meaning                      |
| ---------- | -------------- | -------------- | ---------------------------- |
| `PROVIDER` | codex, grok    | `required`     | Required positional argument |
| `--format` | text, json     | `"text"`       |                              |

### `login`

Authenticate a compatible Model provider.

| Parameter                  | Type / choices | Parser default | Meaning                                                     |
| -------------------------- | -------------- | -------------- | ----------------------------------------------------------- |
| `PROVIDER`                 | codex, grok    | `required`     | Required positional argument                                |
| `--allow-account-switch`   | boolean        | `false`        |                                                             |
| `--device-code, --browser` | boolean        | `true`         | Device authorization (default) or a local browser callback. |
| `--format`                 | text, json     | `"text"`       |                                                             |

## In-chat commands

Type these in the terminal composer, not your shell. Tab completes supported syntax; `/help` and `/?` show native help. There are no `/setup`, `/login`, `/approve`, `/deny`, or `/result` commands. Decisions use their typed selector.

“While busy” means the command parser allows it during work; it is not permission to bypass a pending interaction or operate on an unavailable resource. `/steer` requires a currently steerable root operation. Attachment commands change the draft; active Enter steering accepts text only. See [Use the terminal](everyday-use.md).

| Command grammar                         | Aliases | While busy | Purpose                                                                   |
| --------------------------------------- | ------- | ---------- | ------------------------------------------------------------------------- |
| `/help [command]`                       | `/?`    | Yes        | Show command help and keyboard shortcuts.                                 |
| `/mode [concise\|detailed]`             | —       | Yes        | Switch output detail without changing execution.                          |
| `/theme [auto\|dark\|light]`            | —       | Yes        | Choose a terminal theme.                                                  |
| `/mouse [on\|off]`                      | —       | Yes        | Toggle wheel capture; off preserves native selection/copy.                |
| `/attach path`                          | —       | Yes        | Attach a file or image to the current draft.                              |
| `/paste-image`                          | —       | Yes        | Read clipboard images explicitly.                                         |
| `/remove index\|all`                    | —       | Yes        | Remove one attachment or all attachments from the current draft.          |
| `/recover`                              | —       | No         | Restore an unsent prompt.                                                 |
| `/status`                               | —       | Yes        | Show model, context, environment, and subscription usage.                 |
| `/ps`                                   | —       | Yes        | Inspect observed background processes and their last reported status.     |
| `/subagents [execution-id\|next]`       | —       | Yes        | Inspect this conversation's child executions and retained output.         |
| `/usage [details\|subscription\|reset]` | —       | Yes        | Show recorded Thread usage; subscription/reset inspect Codex limits.      |
| `/steer message`                        | —       | Yes        | Add guidance while the agent is working.                                  |
| `/import`                               | —       | No         | Preview and optionally enable external subagents with parent inheritance. |
| `/agent [agent-id]`                     | —       | No         | Switch agent, including its model, instructions, and tools.               |
| `/model [model-id\|default]`            | —       | No         | Temporarily switch model without changing Agent or saving configuration.  |
| `/fast [on\|off\|reset]`                | —       | No         | Toggle priority service for this session without saving configuration.    |
| `/thinking [level]`                     | —       | No         | Show or change reasoning effort for subsequent turns.                     |
| `/environment [mode]`                   | —       | No         | Show or select execution permissions for subsequent turns.                |
| `/new`                                  | —       | No         | Start a fresh session; keep all saved history.                            |
| `/resume [session-id]`                  | —       | No         | Search, preview, and name saved sessions, or resume one by ID.            |
| `/history`                              | —       | No         | Browse retained messages (Ctrl+T).                                        |
| `/notes`                                | —       | No         | Show saved notes with full contents within display budgets.               |
| `/config`                               | —       | No         | Find your configuration files.                                            |
| `/review request-id`                    | —       | No         | Inspect a pending request against the selected continuation.              |
| `/cancel`                               | —       | Yes        | Stop the current task.                                                    |
| `/quit`                                 | `/exit` | Yes        | End this session.                                                         |

`/thinking` accepts `default`, `low`, `medium`, `high`, or `xhigh`; the actual choices shown depend on the Model. `/environment` selects `full-control` or `sandbox` where supported. Native `/steer` retains its whole trailing message rather than splitting it into shell words.

## Consequential actions and limitations

- `auth key delete REFERENCE --yes` skips the delete confirmation explicitly; it does not make deletion reversible.
- `plugin uninstall ID` removes installed files and edits immediately. It is not merely disabling a source.
- `update` requests installation without a second confirmation. A startup update check still requires confirmation before installation.
- `import subagents` previews unless `--apply` is supplied; imported files are not automatically enrolled in every Agent.
- One-shot `run` does not open interactive approval selectors; suspended/failed operations exit nonzero. Resume interactively for decisions.
- `webui --dangerous-skip-permissions` changes HTTP authentication only, not Agent execution permissions. Read [Browser server](webui.md) before exposing a listener.

## Task-oriented guides

- [Set up a connection](setup.md) and [manage accounts](models-and-authentication.md).
- [Configure resources](configuration.md), [MCP servers](mcp.md), and [Skills/Content Plugins](skills-and-content-plugins.md).
- [Use the terminal](everyday-use.md) for busy input, history, selectors, and keyboard shortcuts.
- [Automate and diagnose](automation-and-troubleshooting.md) for exit/recovery behavior, logs, and interrupted work.
