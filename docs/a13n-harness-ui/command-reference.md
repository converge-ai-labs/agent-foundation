---
title: Command reference
description: Find a shell command or TUI command, its arguments, and the settings it changes.
---

Shell commands start and manage Harness UI. Slash commands control a conversation inside the TUI. Start with the task below, then use the reference for exact arguments.

| I want to…                   | Command                                           | Walkthrough                                         |
| ---------------------------- | ------------------------------------------------- | --------------------------------------------------- |
| Configure my first Agent     | `a13n-harness-ui setup`                           | [First setup](setup.md)                             |
| Chat in the terminal         | `a13n-harness-ui`                                 | [TUI use](everyday-use.md)                          |
| Chat in a browser            | `a13n-harness-ui webui`                           | [WebUI](webui.md)                                   |
| Run one prompt from a script | `a13n-harness-ui run "PROMPT" --format json`      | [Automation](automation-and-troubleshooting.md)     |
| Find or validate my files    | `a13n-harness-ui config path` / `config validate` | [Configuration](configuration.md)                   |
| Inspect local execution      | `a13n-harness-ui environment status`              | [Environments](environments-and-projects.md)        |
| Inspect authentication       | `a13n-harness-ui auth status`                     | [Models and accounts](models-and-authentication.md) |

## Shell invocation

```console
a13n-harness-ui [GLOBAL OPTIONS] [COMMAND] [COMMAND OPTIONS]
```

Put global options before the command. For example, this validates an alternate configuration tree:

```bash
a13n-harness-ui --config ./a13n-harness-ui.yaml config validate
```

Omit `COMMAND` to open the TUI. Use `-h` or `--help` at any level, such as `a13n-harness-ui run --help`. Help and `--version` exit before initializing the App, database, Model, or Environment.

Arguments in capitals are values you supply. Square brackets mark optional arguments in syntax descriptions. Tables distinguish parser defaults from settings loaded from [configuration](configuration.md).

### Global options

| Option                  | Value                     | Effect when supplied                                                                        |
| ----------------------- | ------------------------- | ------------------------------------------------------------------------------------------- |
| `--config`              | YAML path                 | Select a configuration tree; the default root is `~/.a13n-harness-ui/a13n-harness-ui.yaml`. |
| `--data-root`           | Directory path            | Override the local data root.                                                               |
| `--resume`              | Thread ID                 | Open a saved conversation.                                                                  |
| `--agent`               | Agent ID                  | Select the Agent for a new conversation.                                                    |
| `--environment-mode`    | `full-control`, `sandbox` | Select a built-in mode for a new conversation.                                              |
| `--environment-profile` | Profile ID                | Select a custom profile for a new conversation.                                             |
| `--display`             | `concise`, `detailed`     | Override `display.mode`; the configured default is `concise`.                               |
| `--no-update-check`     | Flag                      | Skip startup update detection for this invocation.                                          |
| `--version`             | Flag                      | Print the installed version and exit.                                                       |

These options are unset by default; flags default to false. `--environment-mode` and `--environment-profile` are mutually exclusive. `--resume` cannot be combined with new-conversation Agent, Environment, or title overrides. Resume first, then change the selection in the TUI.

## Commands

Each heading below names a subcommand after `a13n-harness-ui`. Where `--format text|json` is listed, it defaults to `text`. Authentication and plugin commands can change local stores; `config` queries also open application state.

### `webui`

Start the foreground browser server. Complete the [WebUI access setup](webui.md) before exposing it to other machines.

| Option                                                            | Default         | Purpose                                                                      |
| ----------------------------------------------------------------- | --------------- | ---------------------------------------------------------------------------- |
| `--host ADDRESS`                                                  | `127.0.0.1`     | IPv4 or IPv6 listener address.                                               |
| `--port PORT`                                                     | `8765`          | TCP port, from 1 through 65535.                                              |
| `--apikey KEY`, `--api-key KEY`                                   | Unset           | Override `A13N_HARNESS_UI_API_KEY`; the value is visible in shell arguments. |
| `--dangerous-skip-permissions`, `--dangerously-bypass-permission` | Off             | Disable Web authentication, leaving Agent permissions unchanged.             |
| `--share-computer` / `--no-share-computer`                        | Enabled         | Share Host Files, Git Changes, and Terminal as the server OS account.        |
| `--browser-no-sandbox` / `--browser-sandbox`                      | Sandbox enabled | Override `A13N_HARNESS_UI_BROWSER_NO_SANDBOX`.                               |

`--no-share-computer` also disables the Host Browser drawer, discovery, and browser automation. Host sharing is independent of Agent permissions.

Run the browser Host as a non-root user with Chromium's sandbox enabled. Disabling the sandbox is an explicit choice for a separately isolated Host; root is rejected without that choice. `--browser-sandbox` restores protection even when the environment variable disables it. Configure [Browser executable overrides](configuration.md#host-browser) separately.

### `envd update`

Update the native Envd installation without opening the App. Stop daemons using that installation first. With no executable override, the command prepares the managed version selected by the Python client.

| Option              | Default | Purpose                                                                                  |
| ------------------- | ------- | ---------------------------------------------------------------------------------------- |
| `--executable PATH` | Unset   | Update a standalone executable instead of the configured runtime.                        |
| `--version VERSION` | Unset   | Choose an exact native version; managed installations stay pinned to the client version. |
| `--check`           | Off     | Inspect the update without installing.                                                   |
| `--force`           | Off     | Reinstall the selected version; cannot be combined with `--check`.                       |

See [Envd maintenance](automation-and-troubleshooting.md#logs-updates-and-exit) for the update workflow.

### `update`

Upgrade the current uv-tool installation immediately. This command takes no additional arguments and does not ask for a second confirmation. It starts neither the TUI nor setup.

### `setup`

Run the interactive first-setup flow for a Model, context budget, and execution permissions. Add `--advanced` to choose context, reasoning, tool review, subagents, and instructions explicitly.

### `add agent`

Create another Agent while preserving existing Agents and defaults. Add `--advanced` to customize reasoning, tool review, and instructions.

### `add model`

Create a reusable Model without creating or changing an Agent. Add `--advanced` to customize subscription context and reasoning settings.

### `run`

Submit one prompt and exit after its operation. Use `--format json` when a script needs the Thread ID, status, or structured outcome.

```bash
a13n-harness-ui run "Reply with one short sentence." --format json
```

| Argument or option                 | Default  | Purpose                                              |
| ---------------------------------- | -------- | ---------------------------------------------------- |
| `PROMPT`                           | Required | Prompt text; quote it when it contains spaces.       |
| `--resume THREAD_ID`               | Unset    | Continue a saved Thread.                             |
| `--agent AGENT_ID`                 | Unset    | Choose an Agent for a new Thread.                    |
| `--environment-mode MODE`          | Unset    | Choose `full-control` or `sandbox` for a new Thread. |
| `--environment-profile PROFILE_ID` | Unset    | Choose a custom profile for a new Thread.            |
| `--title TITLE`                    | Unset    | Name a new Thread.                                   |
| `--format text\|json`              | `text`   | Choose the output format.                            |

The resume restrictions also apply here. A failed or suspended operation exits nonzero. For a suspended decision, reopen the printed Thread ID interactively; one-shot mode has no approval selector. See [the complete script workflow](automation-and-troubleshooting.md).

### `config path`

Print `configuration_path` and `data_root`. Use the first value to locate the root YAML and its sibling resource directories. Accepts `--format text|json`.

### `config validate`

Validate the selected source tree and report configuration errors or Capability warnings. Accepts `--format text|json`. This checks configuration, not provider or MCP connectivity.

### `config show`

Inspect the accepted configuration. Accepts `--format text|json`. If a file edit failed validation, the accepted generation can differ from the files currently on disk.

### `config subagents`

List the package-owned subagents and whether each is included. Accepts `--format text|json`.

### `import subagents`

Preview conversion of external subagent definitions. Review the candidates before repeating with `--apply` to write ready imports.

```bash
a13n-harness-ui import subagents --product codex --scope project --project-root .
```

| Option                | Default   | Purpose                                       |
| --------------------- | --------- | --------------------------------------------- |
| `--product PRODUCT`   | Required  | `claude-code`, `cursor`, or `codex`.          |
| `--scope SCOPE`       | Required  | `user` or `project`.                          |
| `--project-root PATH` | Unset     | Source directory; required for project scope. |
| `--user-home PATH`    | Your home | Source home directory for user scope.         |
| `--apply`             | Off       | Write every ready candidate.                  |
| `--format text\|json` | `text`    | Choose the output format.                     |

Importing creates files; it does not enroll the imports in every Agent. Follow [Agent configuration](agents-and-subagents.md) to select them.

### `plugin install`

Install a Content Plugin from Git: `plugin install REPOSITORY`. Use `--plugin ID` when the repository has several plugins, and `--ref REF` to select a branch, tag, or commit. Both options are unset by default. Accepts `--format text|json`.

### `plugin list`

List installed Content Plugins and their editable directories. Accepts `--format text|json`.

### `plugin uninstall`

Delete a plugin with `plugin uninstall PLUGIN_ID`. This permanently removes its installed directory, including local edits, without another confirmation. Back up edits before running it. Accepts `--format text|json`.

### `environment list`

List available Environment modes and profiles. Accepts `--format text|json`.

### `environment status`

Inspect the local Envd installation and runtime state. This performs no download, daemon start, or desktop permission request. Accepts `--format text|json`.

### `environment check`

Prepare a built-in profile and check readiness without changing the saved default. Preparation can download the matching Envd version.

```bash
a13n-harness-ui environment check --profile environment-envd
```

`--profile` accepts `environment-native`, `environment-envd`, `environment-desktop`, or `environment-sandbox`; the default is `environment-envd`. Also accepts `--format text|json`. Exit code `0` means ready; `1` means preparation failed.

For `environment-desktop`, first enable Computer Use in Runtime settings and restart Harness UI. The check may request OS permissions on the Harness Host. See [local Envd setup](environments-and-projects.md#manage-local-envd).

### `environment configure`

Prepare a profile, then save it as the new-conversation default. `--profile` is required and uses the same choices as `environment check`. Also accepts `--format text|json`.

Success exits `0`; preparation failure exits `1` and preserves the old default. Existing Threads keep their selections. Desktop preparation has the same prerequisites as `environment check`.

### `doctor`

Inspect App and extension diagnostics. Accepts `--format text|json`. Use [troubleshooting](automation-and-troubleshooting.md) to choose the next check from the reported symptom.

### `auth status`

Inspect Model authentication: `auth status [PROVIDER]`. The optional provider is `chatgpt`, `codex`, `grok`, or `copilot`. Accepts `--format text|json`.

### `auth key list`

List saved API-key references without returning secret values. Accepts `--format text|json`.

### `auth key set`

Store a key with `auth key set REFERENCE`, for example `auth key set key-primary`. Enter the secret at the hidden prompt; the command line contains only the reference.

### `auth key delete`

Delete a key with `auth key delete REFERENCE`. The command asks for confirmation unless you add `--yes`. Models that refer to the deleted key will need a replacement before they can authenticate.

### `auth sources`

List account sources with `auth sources PROVIDER`. The parser accepts `chatgpt`, `codex`, `grok`, and `copilot`; Copilot supports source/account selection, and ChatGPT supports native registration selection. Accepts `--format text|json` and does not reveal credentials.

### `auth select`

Replace the account/source binding in this data root. First run `auth sources copilot`, then select a listed login:

```bash
a13n-harness-ui auth select copilot --source native --account YOUR_GITHUB_LOGIN
```

`PROVIDER`, `--source`, and `--account` are required. `--source` accepts `native` or `copilot_cli_file`; the Host resolves the file path. Provider syntax is the same as `auth sources`. Copilot supports both sources; ChatGPT uses `native` with a registration key returned by `auth sources chatgpt`. Accepts `--format text|json`.

### `auth logout`

Remove selected local credentials with `auth logout PROVIDER`, using `chatgpt`, `codex`, `grok`, or `copilot`. Accepts `--format text|json`.

For a shared Copilot CLI file source, logout also removes the selected account's supported token fields from that file, affecting Copilot CLI. Other accounts stay intact; the Host does not select a fallback source.

### `login`

Authenticate with `login PROVIDER`, using `chatgpt`, `codex`, `grok`, or `copilot`.

| Option                        | Default         | Purpose                                                  |
| ----------------------------- | --------------- | -------------------------------------------------------- |
| `--allow-account-switch`      | Off             | Permit replacement of a different stored account.        |
| `--device-code` / `--browser` | `--device-code` | Choose device authorization or a local browser callback. |
| `--format text\|json`         | `text`          | Choose the output format.                                |

ChatGPT uses callback-URL paste for the default flow. Local browser callbacks are supported for Codex and Grok. Copilot supports device authorization only and uses the public Copilot App identity by default; subscribers need not register an OAuth App. See [Copilot setup](models-and-authentication.md#github-copilot-subscription) for permissions and compatibility.

## Slash commands

Type these in the TUI composer. Use Tab to complete arguments, `/help` or `/?` for help, and `/help COMMAND` for one command. Setup and login run in your shell; pending decisions use the TUI's typed selectors rather than `/approve`, `/deny`, or `/result`.

The tables group commands by task. “Busy” means the parser allows the command during work; the target must still be available. Enter while busy sends draft text and attachments as steering.

### Conversation and input

| Command                  | Busy | Use                                                           |
| ------------------------ | ---- | ------------------------------------------------------------- |
| `/new`                   | No   | Start a new conversation; retain saved history.               |
| `/resume [thread-id]`    | No   | Search, preview, name, or reopen a saved conversation.        |
| `/history`               | No   | Browse retained messages; also available with Ctrl+T.         |
| `/notes`                 | No   | Read saved notes within display budgets.                      |
| `/goal task description` | No   | Start goal-driven work with audits and bounded continuations. |
| `/attach path`           | Yes  | Add a file or image to the draft.                             |
| `/paste-image`           | Yes  | Read an image from the TUI Host's clipboard.                  |
| `/recover`               | No   | Restore an unsent prompt.                                     |
| `/review request-id`     | No   | Inspect a pending request against the selected continuation.  |
| `/steer message`         | Yes  | Send guidance to a steerable root operation.                  |
| `/cancel`                | Yes  | Stop the active Run.                                          |
| `/quit`, `/exit`         | Yes  | Exit the TUI; retain saved conversation state.                |

`/steer` preserves its trailing message, including quotes and line breaks. Steering follows global `input.steering_mode`: immediate interruption or cooperative handoff by default, or `queued` for the next safe boundary. `/cancel` also suppresses automatic tool-completion wake until new input; it does not replace separate tool cancellation.

### Model and Environment selection

| Command                                | Busy | Use                                                                               |
| -------------------------------------- | ---- | --------------------------------------------------------------------------------- |
| `/agent [agent-id]`                    | No   | Select an Agent with its Model, instructions, and tools.                          |
| `/model [model-id\|default\|defaults]` | No   | Override the Model, restore the default, or configure global media understanding. |
| `/fast [on\|off\|ultrafast\|reset]`    | No   | Choose session-local Fast/Ultrafast settings.                                     |
| `/pro [on\|off\|reset]`                | No   | Choose Pro/Standard reasoning or inherit the Model.                               |
| `/thinking [level]`                    | No   | Choose the Model's supported reasoning effort.                                    |
| `/environment [mode]`                  | No   | Change local execution for subsequent turns.                                      |
| `/import`                              | No   | Preview and optionally enable external subagents with parent inheritance.         |
| `/config`                              | No   | Locate configuration files.                                                       |

`/model` remembers the override for the Project; `default` clears the session override and remembered choice. `defaults` opens the global media-understanding editor with Save/Cancel. Fast choices remain session-local.

`/thinking` and completion use the selected Model's supported choices. `default` inherits configured settings. Depending on the adapter, explicit levels can include `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, and `max`. Unknown models offer only `default` with an explanation; unsupported levels are rejected.

`/environment` accepts `full-control`, `envd`, `desktop`, or `sandbox` where supported. `envd` and `desktop` prepare the runtime before changing the Thread's next-turn profile; failure preserves the selection. This changes the current Thread, not the new-conversation default.

### Status and presentation

| Command                                                                 | Busy | Use                                                                                    |
| ----------------------------------------------------------------------- | ---- | -------------------------------------------------------------------------------------- |
| `/help [command]`, `/?`                                                 | Yes  | Show help and shortcuts.                                                               |
| `/mode [concise\|detailed]`                                             | Yes  | Change output detail, not execution.                                                   |
| `/theme [auto\|dark\|light]`                                            | Yes  | Change the TUI theme.                                                                  |
| `/mouse [on\|off]`                                                      | Yes  | Toggle wheel capture; `off` allows native text selection/copy.                         |
| `/status`                                                               | Yes  | Inspect Model, context, Environment, and subscription usage.                           |
| `/ps`                                                                   | Yes  | Inspect observed processes and last reported status.                                   |
| `/executions [offset\|cancel execution-id]`                             | Yes  | Page through retained tool executions or request cancellation.                         |
| `/subagents [execution-id\|next]`                                       | Yes  | Inspect child executions and retained output.                                          |
| `/usage [all\|root\|subagents\|details\|subscription\|accounts\|reset]` | Yes  | Inspect Thread usage, subscription limits, accounts, or confirmed Codex reset credits. |

`/usage details` adds Run attribution; `all`, `root`, and `subagents` select usage scope. Subscription limits are separate from Thread token and cost totals. `/executions` reads process-local status without consuming results; `cancel execution-id` requests cancellation of that retained execution. See [tool execution controls](webui.md#tool-executions-and-immediate-steering).

## Consequential actions and limitations

| Action                               | Before you use it                                                                              |
| ------------------------------------ | ---------------------------------------------------------------------------------------------- |
| `plugin uninstall`                   | Back up local plugin edits; deletion is immediate.                                             |
| `auth key delete --yes`              | Confirm the reference; this flag skips the prompt.                                             |
| `update`, `envd update`              | Expect installation without a second confirmation. Detection alone does not install an update. |
| `import subagents --apply`           | Inspect the preview first; this writes imported files.                                         |
| `run`                                | Plan to reopen suspended work interactively for decisions.                                     |
| `webui --dangerous-skip-permissions` | Understand that this removes HTTP authentication, not Agent permission checks.                 |

## Task-oriented guides

- [Setup](setup.md): connect a Model and send a first prompt.
- [Configuration](configuration.md): locate files, edit resources, and validate changes.
- [Models and authentication](models-and-authentication.md): choose routes and manage credentials.
- [MCP](mcp.md) and [Skills/Content Plugins](skills-and-content-plugins.md): add integrations.
- [TUI use](everyday-use.md): compose input, inspect history, and handle decisions.
- [Automation and troubleshooting](automation-and-troubleshooting.md): inspect outcomes, diagnose failures, and resume saved work.
