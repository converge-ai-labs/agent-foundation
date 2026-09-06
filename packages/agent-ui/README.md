# Agent UI

`a13n-ui` is a local single-user workstation for Agent Foundation Harness. CLI, TUI, and WebUI adapters share one process-local `AgentUiApp`; Harness execution, mutable continuation-backed Threads, async subagents, and live presentation run in that process. Agent UI does not start or supervise a replaceable Runner process and does not hot-reload imported Python extension code.

Bare invocation and the canonical `tui` subcommand start the same terminal workstation. The browser surface is always explicit:

```console
a13n-ui
a13n-ui tui
a13n-ui webui
```

TUI launch options override only the new-Thread draft for that invocation; they do not edit file defaults:

```console
a13n-ui --project project-main --agent agent-assistant --environment-mode sandbox
a13n-ui tui --thread thread-existing
```

A one-shot Run can use the same human-editable configuration tree:

```console
a13n-ui --config ~/.a13n-ui/a13n-ui.yaml run "Inspect the Agent behavior"
```

Projects are the only local-root grouping concept. Every root or child Thread owns an immutable Project identity and versioned mutable non-Project selections, while each admitted Run captures an immutable resolved composition. CLI, TUI, WebUI, and model-visible tools call the same application boundary. Only WebUI-hosted roots receive `ThreadCollaborationCapability`; CLI/TUI roots and children do not. The exact accepted behavior and migration target are owned by the [Agent UI specification](../../spec/agent-ui/README.md).

The repository Make alias starts the interactive TUI:

```console
make a13n-ui
```

After publication, the distribution and console entrypoint share the same name:

```console
uvx a13n-ui
uvx a13n-ui tui
```

The repository directory is `packages/agent-ui`, the Python distribution is `a13n-ui`, and the import package is `a13n_ui`. The private browser source lives in [`apps/harness-ui`](../../apps/harness-ui/README.md).

## Terminal Workstation

The TUI keeps one process-local App open and shows one conversation with its timeline, composer, decisions, and exact operation controls. `Ctrl+O` opens a transient navigation-only Thread picker with bounded title/ID search, current-Project or All-Projects scope, and pagination. There is no Workbench, split preview, second composer, or mode toggle. Other root Runs and child executions continue while their Thread is not selected.

`Ctrl+N` starts a new draft and `Ctrl+P` opens the command palette. `Escape` closes the top completion, picker, or review without approving, denying, cancelling, or clearing a draft. `Ctrl+C` first closes a transient surface, otherwise requests cancellation for the focused active root operation, and exits when there is nothing to cancel. Every workflow also exposes selectable controls for mouse use.

The composer accepts the following terminal commands; the command palette exposes the same registry and disables actions that are unavailable in the current state:

| Commands                                           | Purpose                                                                                     |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `/new`, `/threads`, `/setup`                       | Create a draft, find a Thread, or reopen first-use setup.                                   |
| `/skills`, `/agent`, `/environment`, `/extensions` | Insert an effective Skill reference or select accepted configuration for a draft or Thread. |
| `/status`, `/details`, `/thinking`, `/help`        | Inspect state and control progressive disclosure.                                           |
| `/editor`, `/cancel`, `/archive`, `/exit`          | Hand off a draft, control exact work, archive an inactive Thread, or leave the App.         |

Ordinary input during an active root Run is an exact steering attempt, never a hidden next-turn queue. `@` completion resolves App-owned Project logical paths and `$` completion resolves exact effective Skill references. Structured questions and approvals remain bound to one selected continuation and are submitted as one explicit response batch. Tool, diff, task, and child details open as bounded review surfaces; omitted or truncated content is labeled rather than inferred.

The external editor action resolves `$VISUAL` and then `$EDITOR`, parses the command without a shell, suspends Textual while the editor owns the terminal, and applies the returned text only after a successful editor exit. The original draft survives an unavailable editor or non-zero exit.

Wide terminals can show the conversation inspector; the picker remains a transient navigation overlay. Medium terminals reduce secondary detail, and narrow terminals use the primary single-pane flow with full-screen overlays. Resizing preserves the selected Thread, draft, overlay purpose, timeline selection, and reading state. If exit would interrupt active process-local root or child work, the TUI shows the active counts and requires explicit confirmation; that work does not detach or continue after App shutdown. Textual restores normal terminal mode before the launcher waits for App cleanup.

## Setup and Browser

First use has three steps: model connection (API-key environment reference, compatible Codex/Grok subscription, or Not now), execution environment, and default Agent. Not now still creates a default Agent without a Model; it cannot run until configured. Every Agent receives the built-in system prompt; optional user `instructions` are additional and never replace it. Complete file preview precedes confirmed publication, and existing files remain unchanged. Subscription discovery initially selects all usable providers. Codex defaults to Terra with a low-thinking Luna shell reviewer; Grok defaults to Grok 4.6. Direct API-key storage and in-wizard OAuth presentation are not implemented; subscription login uses the existing auth CLI.

Sandbox selection runs a cancellable production-equivalent preflight. Failed checks offer Retry, Cancel, or explicit Full Control without changing system security policy. Windows Sandbox isolation is unsupported; Full Control uses PowerShell and requires explicit selection. See the [user guide](../../docs/agent-ui.md) for setup, recovery, and environment authority.

`a13n-ui webui` starts one foreground HTTP/SSE server that directly owns its App in memory. No separate daemon, IPC, or CLI bridge is involved. The browser has one conversation, a transient picker, and setup. Closing a tab only closes delivery; stopping the server shuts down the App. A generated API key is printed as terminal-only bootstrap material. General-purpose guided Settings editors and advanced child controls remain outside this browser implementation; desired resources remain editable files.

## CLI Discovery and Output

Run `a13n-ui --help` to discover top-level commands and `a13n-ui COMMAND --help` or `a13n-ui GROUP COMMAND --help` for command-specific options. Management groups cover configuration, imports, Content Plugins, Threads, Projects, Environments, model authentication, and one-shot execution.

Non-interactive commands that return data accept `--format text` for readable labeled output or `--format json` for compact machine-readable output. Successful JSON is emitted on stdout. Validation and application errors return a non-zero status, with text diagnostics on stderr or machine-readable JSON envelopes on stdout. Click rejects invalid options and arguments with command-local usage guidance on stderr before application startup.

```console
a13n-ui config validate
a13n-ui project list --format json
a13n-ui thread list --include-archived
a13n-ui run "Inspect the current project" --format text
```

## Configuration

Agent UI selects a root YAML from explicit `--config PATH` or the platform user path, `~/.a13n-ui/a13n-ui.yaml` on Unix-like systems. Fixed immediate sibling directories contain one YAML resource per Model, extension, MCP server, Agent, or Project, plus one canonical Markdown file per `subagents/` definition. Direct editing remains a complete configuration path; valid changes reload without restarting imported Python code.

CLI and WebUI file mutations require expected source digests and reject stale writes. SQLite stores accepted-generation indexes and mutable Thread/runtime heads, but files remain desired-configuration authority. The data root is resolved before root-YAML parsing from `--data-root`, `A13N_UI_DATA_ROOT`, or the config directory's `data/` default.

Agent UI includes two fixed Environment modes. **Full Control** uses Direct Local Host execution. **Sandbox** shows the same canonical Host Project paths to the Agent but executes Project commands through Local Envd over EIP with required native isolation and denied networking.

### Automatic Model Prices

CLI, TUI, and WebUI App lifetimes enable Pydantic AI's background price updates by default. Startup uses bundled prices immediately; successful downloads are adopted by subsequent root and async-child Agent builds. Existing Agents and usage records are not repriced. Download or catalog-conversion failure keeps the last valid prices, and no disk price cache is created.

Disable downloads in the selected root YAML and restart the App:

```yaml
schema_version: "2"
process:
  pricing_auto_update: false
```

This controls the updater owned by this App; it does not erase prices already downloaded by another updater in the same process. The setting does not change Agent model selection, context windows, or provider request parameters. See [Harness pricing](../../docs/agent-harness/agents-and-runs.md#keep-prices-current-in-a-host) for embedded integrations and explicit bundled snapshots.

## Content Plugins

Declarative Content Plugins install Skills and canonical Markdown subagents from a Git repository without importing or executing plugin code. A repository contains `.agents/plugins/marketplace.yaml`; each indexed plugin contains `.a13n-plugin/plugin.yaml` and can point to `skills/` and `subagents/` directories.

```console
a13n-ui plugin install https://github.com/example/agent-plugins.git
a13n-ui plugin install https://github.com/example/agent-plugins.git --plugin plugin-reviewer --ref v1.0.0
a13n-ui plugin list
a13n-ui plugin uninstall plugin-reviewer
```

Install and list output includes the immutable installation directory so its complete content can be inspected directly. Uninstall removes the registration but retains that content-addressed directory for Runs that already captured it. Content Plugin management is CLI-only; there is no WebUI management surface.

## Local Store Development

Agent UI owns its SQLite schema and Alembic history independently from Foundation Service. Before the first published Agent UI release, an unreleased history may be squashed to one generated base revision because no supported user database depends on its revision IDs. After publication, retain revision identity and generate additive revisions. Generate every reviewed revision from the repository root against a disposable SQLite database:

```console
make agent-ui-db-migrate msg="describe the schema change"
```

The generator upgrades the disposable database to the current package head before comparing it with Agent UI metadata. Application startup only applies committed migrations; it never autogenerates against a user's data root.

## Dependencies

The source manifest declares unversioned dependencies on `a13n-environment-provider`, `a13n-harness`, and `a13n-stream-protocol`, so uv resolves all three from the workspace during repository development. Before tagging an Agent UI release, set `[tool.a13n.agent-ui-release].harness-version` to one published canonical Harness release such as `1.2.3` or `1.2.3-rc.1`; the `0.0.0` placeholder blocks a real release. Agent UI release automation pins all three dependencies to that exact normalized Python version before building publishable artifacts.

## Browser Assets

Compiled frontend files are not committed to Git. Repository builds compile Harness UI and copy it into the generated `a13n_ui/static/` tree before building Python artifacts. Both the sdist and wheel contain those files, and rebuilding a wheel from the sdist does not require Node.js. A source-checkout package build fails when the assets have not been prepared.

## Versioning

Agent UI releases independently through `release/agent-ui-v<version>`, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Its version does not need to match the selected Harness release; Python package metadata represents either RC as `X.Y.ZrcN`. Harness UI has no independent npm artifact, version, tag, or release workflow.

The accepted architecture is defined in the [Agent UI specification](../../spec/agent-ui/README.md).
