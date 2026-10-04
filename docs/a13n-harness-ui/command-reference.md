---
title: Command reference
description: Every shell command, option, and TUI slash command.
---

Use shell commands to start or manage Harness UI; use slash commands in the TUI. For a workflow rather than a syntax lookup, start with [setup](setup.md) or [TUI use](everyday-use.md).

## Shell invocation

```console
a13n-harness-ui [GLOBAL OPTIONS] [COMMAND] [COMMAND OPTIONS]
```

Omitting `COMMAND` opens the TUI. Put global options before the command; use `-h` or `--help` at each level. `--version` prints the version and exits before any command runs. Help and version do not initialize the App, database, Model, or Environment.

These tables show command-line defaults; [Configuration](configuration.md) explains effective defaults. A conversation is a root Thread; `--resume` takes its Thread ID and cannot be combined with new-conversation Agent, Environment, or title overrides.

### Global options

| Parameter               | Type / choices        | Parser default | Meaning                                                                                    |
| ----------------------- | --------------------- | -------------- | ------------------------------------------------------------------------------------------ |
| `--config`              | path                  | not set        | Explicit Harness UI configuration YAML (default: ~/.a13n-harness-ui/a13n-harness-ui.yaml). |
| `--data-root`           | path                  | not set        | Override the local Harness UI data root.                                                   |
| `--resume`              | text                  | not set        | Resume a saved conversation by Thread ID.                                                  |
| `--agent`               | text                  | not set        | Select a configured Agent for new conversations.                                           |
| `--environment-mode`    | full-control, sandbox | not set        | Override the built-in Environment mode for new conversations.                              |
| `--environment-profile` | text                  | not set        | Override the custom Environment profile for new conversations.                             |
| `--display`             | concise, detailed     | `not set`      | Presentation only; overrides display.mode (default concise). /mode switches live.          |
| `--no-update-check`     | boolean               | `false`        | Skip startup update detection for this invocation.                                         |
| `--version`             | boolean               | `false`        | Show the version and exit.                                                                 |

## Commands

Place global options before these subcommands. Authentication and plugin commands change local stores; `config` inspection also opens application state.

### `webui`

Run one foreground WebUI server with bundled browser assets.

| Parameter                                                       | Type / choices   | Parser default | Meaning                                                                                                               |
| --------------------------------------------------------------- | ---------------- | -------------- | --------------------------------------------------------------------------------------------------------------------- |
| `--host`                                                        | text             | `"127.0.0.1"`  | Listener IPv4 or IPv6 address.                                                                                        |
| `--port`                                                        | integer 1..65535 | `8765`         | Listener TCP port.                                                                                                    |
| `--apikey, --api-key`                                           | text             | not set        | Listener API key; overrides A13N_HARNESS_UI_API_KEY (visible in shell arguments).                                     |
| `--dangerous-skip-permissions, --dangerously-bypass-permission` | boolean          | `false`        | Disable Web authentication only; does not change Agent permissions.                                                   |
| `--share-computer` / `--no-share-computer`                      | boolean          | `true`         | Share native Host Files, Git Changes, and Terminal panels as the server OS account, independent of Agent permissions. |

### `update`

Update this uv-tool installation now, without starting the TUI or setup.

No command-specific parameters.

### `setup`

Configure a Model, context budget, and execution permissions interactively.

| Parameter    | Type / choices | Parser default | Meaning                                                                   |
| ------------ | -------------- | -------------- | ------------------------------------------------------------------------- |
| `--advanced` | boolean        | `false`        | Also choose context, reasoning, tool review, subagents, and instructions. |

### `add agent`

Create another Agent without changing existing Agents or defaults.

| Parameter    | Type / choices | Parser default | Meaning                                                  |
| ------------ | -------------- | -------------- | -------------------------------------------------------- |
| `--advanced` | boolean        | `false`        | Also customize reasoning, tool review, and instructions. |

### `add model`

Create a reusable Model without creating or changing an Agent.

| Parameter    | Type / choices | Parser default | Meaning                                                     |
| ------------ | -------------- | -------------- | ----------------------------------------------------------- |
| `--advanced` | boolean        | `false`        | Also customize subscription context and reasoning settings. |

### `run`

Execute one prompt without opening the TUI.

| Parameter               | Type / choices        | Parser default | Meaning                                                 |
| ----------------------- | --------------------- | -------------- | ------------------------------------------------------- |
| `PROMPT`                | text                  | `required`     | Required positional argument                            |
| `--resume`              | text                  | not set        | Continue a saved conversation by Thread ID.             |
| `--agent`               | text                  | not set        | Agent used for a new conversation.                      |
| `--environment-mode`    | full-control, sandbox | not set        | Built-in Environment mode used for a new conversation.  |
| `--environment-profile` | text                  | not set        | Custom Environment profile used for a new conversation. |
| `--title`               | text                  | not set        | Title used for a new conversation.                      |
| `--format`              | text, json            | `"text"`       | Output format                                           |

### `config path`

Show the selected configuration and data paths.

| Parameter  | Type / choices | Parser default | Meaning       |
| ---------- | -------------- | -------------- | ------------- |
| `--format` | text, json     | `"text"`       | Output format |

### `config validate`

Validate the selected source tree.

| Parameter  | Type / choices | Parser default | Meaning       |
| ---------- | -------------- | -------------- | ------------- |
| `--format` | text, json     | `"text"`       | Output format |

### `config show`

Show the accepted configuration.

| Parameter  | Type / choices | Parser default | Meaning       |
| ---------- | -------------- | -------------- | ------------- |
| `--format` | text, json     | `"text"`       | Output format |

### `config subagents`

List package-owned subagents and their current inclusion.

| Parameter  | Type / choices | Parser default | Meaning       |
| ---------- | -------------- | -------------- | ------------- |
| `--format` | text, json     | `"text"`       | Output format |

### `import subagents`

Preview or apply external subagent imports.

| Parameter        | Type / choices             | Parser default | Meaning                                                               |
| ---------------- | -------------------------- | -------------- | --------------------------------------------------------------------- |
| `--product`      | claude-code, cursor, codex | `required`     | Product whose subagent definitions to read.                           |
| `--scope`        | user, project              | `required`     | Read user-level or project-level definitions.                         |
| `--project-root` | path                       | not set        | Directory to read for project scope; required with that scope.        |
| `--user-home`    | path                       | not set        | Home directory to read for user scope (default: your home directory). |
| `--apply`        | boolean                    | `false`        | Apply every ready candidate; omission is a dry-run preview.           |
| `--format`       | text, json                 | `"text"`       | Output format                                                         |

### `plugin install`

Install one Content Plugin from a Git repository.

| Parameter    | Type / choices | Parser default | Meaning                                         |
| ------------ | -------------- | -------------- | ----------------------------------------------- |
| `REPOSITORY` | text           | `required`     | Required positional argument                    |
| `--plugin`   | text           | not set        | Plugin ID when the repository contains several. |
| `--ref`      | text           | not set        | Git branch, tag, or commit to install.          |
| `--format`   | text, json     | `"text"`       | Output format                                   |

### `plugin list`

List installed Content Plugins and their directories.

| Parameter  | Type / choices | Parser default | Meaning       |
| ---------- | -------------- | -------------- | ------------- |
| `--format` | text, json     | `"text"`       | Output format |

### `plugin uninstall`

Permanently delete the installed Content Plugin directory, including local edits, without another confirmation. Back up edits first; reinstall does not restore them.

| Parameter   | Type / choices | Parser default | Meaning                      |
| ----------- | -------------- | -------------- | ---------------------------- |
| `PLUGIN_ID` | text           | `required`     | Required positional argument |
| `--format`  | text, json     | `"text"`       | Output format                |

### `environment list`

List Environment modes and profiles.

| Parameter  | Type / choices | Parser default | Meaning       |
| ---------- | -------------- | -------------- | ------------- |
| `--format` | text, json     | `"text"`       | Output format |

### `doctor`

Inspect App and extension health.

| Parameter  | Type / choices | Parser default | Meaning       |
| ---------- | -------------- | -------------- | ------------- |
| `--format` | text, json     | `"text"`       | Output format |

### `auth status`

Show Model authentication status.

| Parameter  | Type / choices                | Parser default | Meaning                      |
| ---------- | ----------------------------- | -------------- | ---------------------------- |
| `PROVIDER` | chatgpt, codex, grok, copilot | `not set`      | Optional positional argument |
| `--format` | text, json                    | `"text"`       | Output format                |

### `auth key list`

List saved key references without returning their key bytes.

| Parameter  | Type / choices | Parser default | Meaning       |
| ---------- | -------------- | -------------- | ------------- |
| `--format` | text, json     | `"text"`       | Output format |

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

### `auth sources`

List supported saved account sources without exposing credentials. Source selection is currently supported for Copilot only.

| Parameter  | Type / choices                | Parser default | Meaning                      |
| ---------- | ----------------------------- | -------------- | ---------------------------- |
| `PROVIDER` | chatgpt, codex, grok, copilot | `required`     | Required positional argument |
| `--format` | text, json                    | `"text"`       | Output format                |

### `auth select`

Explicitly replace the account and credential-source binding stored in this data root. Use a login listed by `auth sources copilot`; source paths are resolved by the Host, not supplied on the command line.

| Parameter   | Type / choices                | Parser default | Meaning                                                                 |
| ----------- | ----------------------------- | -------------- | ----------------------------------------------------------------------- |
| `PROVIDER`  | chatgpt, codex, grok, copilot | `required`     | Required positional argument; currently only Copilot supports selection |
| `--source`  | native, copilot_cli_file      | `required`     | Credential source kind                                                  |
| `--account` | text                          | `required`     | Selected GitHub login                                                   |
| `--format`  | text, json                    | `"text"`       | Output format                                                           |

### `auth logout`

Remove the selected locally stored Model credentials. For a shared Copilot CLI file source, this also deletes the selected account's supported token fields from that file and affects Copilot CLI. Other accounts are preserved; the Host does not fall back to another source.

| Parameter  | Type / choices                | Parser default | Meaning                      |
| ---------- | ----------------------------- | -------------- | ---------------------------- |
| `PROVIDER` | chatgpt, codex, grok, copilot | `required`     | Required positional argument |
| `--format` | text, json                    | `"text"`       | Output format                |

### `login`

Authenticate a compatible Model provider. Copilot supports device authorization only and defaults to the public Copilot App identity; subscribers do not need to register an OAuth App. See [models and authentication](models-and-authentication.md#github-copilot-subscription) for permissions and compatibility limits.

| Parameter                  | Type / choices                | Parser default | Meaning                                                                                                            |
| -------------------------- | ----------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------ |
| `PROVIDER`                 | chatgpt, codex, grok, copilot | `required`     | Required positional argument                                                                                       |
| `--allow-account-switch`   | boolean                       | `false`        | Allow login to replace a different account already stored for this provider.                                       |
| `--device-code, --browser` | boolean                       | `true`         | Device authorization (default; ChatGPT uses callback URL paste) or a local browser callback (Codex and Grok only). |
| `--format`                 | text, json                    | `"text"`       | Output format                                                                                                      |

## Slash commands

Type these in the TUI composer, not your shell. Tab completes supported syntax; `/help` and `/?` show native help. There are no `/setup`, `/login`, `/approve`, `/deny`, or `/result` commands. Decisions use their typed selector.

“While busy” means the command parser allows it during work; it is not permission to bypass a pending interaction or operate on an unavailable resource. `/steer` requires a currently steerable root operation. Attachment commands change the draft. Pressing Enter while the Agent is working sends the draft text and attachments together as steering. See [TUI use](everyday-use.md).

| Command grammar                         | Aliases | While busy | Purpose                                                                                                                                                                                    |
| --------------------------------------- | ------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `/help [command]`                       | `/?`    | Yes        | Show command help and keyboard shortcuts.                                                                                                                                                  |
| `/mode [concise\|detailed]`             | —       | Yes        | Switch output detail without changing execution.                                                                                                                                           |
| `/theme [auto\|dark\|light]`            | —       | Yes        | Choose the TUI theme.                                                                                                                                                                      |
| `/mouse [on\|off]`                      | —       | Yes        | Toggle wheel capture; off preserves native selection/copy.                                                                                                                                 |
| `/attach path`                          | —       | Yes        | Attach a file or image to the current draft.                                                                                                                                               |
| `/paste-image`                          | —       | Yes        | Read clipboard images explicitly.                                                                                                                                                          |
| `/recover`                              | —       | No         | Restore an unsent prompt.                                                                                                                                                                  |
| `/status`                               | —       | Yes        | Show Model, context, Environment, and subscription usage.                                                                                                                                  |
| `/ps`                                   | —       | Yes        | Inspect observed background processes and their last reported status.                                                                                                                      |
| `/subagents [execution-id\|next]`       | —       | Yes        | Inspect this conversation's child executions and retained output.                                                                                                                          |
| `/usage [details\|subscription\|reset]` | —       | Yes        | Show recorded Thread usage; subscription shows Codex plan limits; reset redeems a Codex reset credit after confirmation.                                                                   |
| `/goal task description`                | —       | No         | Work toward a goal with completion audits and bounded continuations.                                                                                                                       |
| `/steer message`                        | —       | Yes        | Steer the active Run with additional input.                                                                                                                                                |
| `/import`                               | —       | No         | Preview and optionally enable external subagents with parent inheritance.                                                                                                                  |
| `/agent [agent-id]`                     | —       | No         | Switch Agent, including its Model, instructions, and tools.                                                                                                                                |
| `/model [model-id\|default\|defaults]`  | —       | No         | Override the Model until you quit and remember it for the Project; default clears the override and the remembered choice; defaults configures global media understanding with Save/Cancel. |
| `/fast [on\|off\|ultrafast\|reset]`     | —       | No         | Select Fast or Ultrafast until you quit, without saving configuration.                                                                                                                     |
| `/pro [on\|off\|reset]`                 | —       | No         | Select Pro reasoning mode; off selects Standard, reset inherits the Model.                                                                                                                 |
| `/thinking [level]`                     | —       | No         | Show or change reasoning effort for subsequent turns.                                                                                                                                      |
| `/environment [mode]`                   | —       | No         | Show or select the Environment mode (Full Control or Sandbox) for subsequent turns.                                                                                                        |
| `/new`                                  | —       | No         | Start a new conversation; keep all saved history.                                                                                                                                          |
| `/resume [thread-id]`                   | —       | No         | Search, preview, and name saved conversations, or resume one by Thread ID.                                                                                                                 |
| `/history`                              | —       | No         | Browse retained messages (Ctrl+T).                                                                                                                                                         |
| `/notes`                                | —       | No         | Show saved notes with full contents within display budgets.                                                                                                                                |
| `/config`                               | —       | No         | Find your configuration files.                                                                                                                                                             |
| `/review request-id`                    | —       | No         | Inspect a pending request against the selected continuation.                                                                                                                               |
| `/cancel`                               | —       | Yes        | Stop the active Run.                                                                                                                                                                       |
| `/quit`                                 | `/exit` | Yes        | Quit the TUI; the saved conversation stays resumable.                                                                                                                                      |

`/thinking` opens the selected Model's supported choices; command completion uses the same list. `default` inherits the Model's configured settings. Depending on the model and adapter, explicit choices can include `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`. Unsupported choices are rejected, and unknown models offer only default with an explanation. `/environment` selects `full-control` or `sandbox` where supported. Native `/steer` retains its whole trailing message rather than splitting it into shell words.

## Consequential actions and limitations

- `auth key delete REFERENCE --yes` skips the delete confirmation explicitly; it does not make deletion reversible.
- `plugin uninstall ID` removes installed files and edits immediately. It is not merely disabling a source.
- `update` requests installation without a second confirmation. A startup update check still requires confirmation before installation.
- `import subagents` previews unless `--apply` is supplied; imported files are not automatically enrolled in every Agent.
- One-shot `run` does not open interactive approval selectors; suspended/failed operations exit nonzero. Resume interactively for decisions.
- `webui --dangerous-skip-permissions` changes HTTP authentication only, not Agent execution permissions. Read [WebUI](webui.md) before exposing a listener.

## Task-oriented guides

- [Set up a connection](setup.md) and [manage accounts](models-and-authentication.md).
- [Configure resources](configuration.md), [MCP servers](mcp.md), and [Skills/Content Plugins](skills-and-content-plugins.md).
- [Use the TUI](everyday-use.md) for busy input, history, selectors, and keyboard shortcuts.
- [Automate and diagnose](automation-and-troubleshooting.md) for exit/recovery behavior, logs, and interrupted work.
