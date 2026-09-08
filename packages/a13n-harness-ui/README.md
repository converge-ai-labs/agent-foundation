# Harness UI

`a13n-harness-ui` is the interactive coding CLI supplied by the `a13n-harness-ui` distribution. It uses a native full-terminal Markdown viewport, an editable multiline/image draft, and a compact status bar. One reusable `HarnessUiApp` owns execution, continuation-backed history, async subagents, decisions, and live events. `a13n-harness-ui webui` starts the HTTP API and bundled Hello World page in a foreground server process; browser chat and management are not implemented. There is no detached daemon.

## Install and Run

Install the published CLI in an isolated tool environment with [`uv`](https://docs.astral.sh/uv/getting-started/installation/):

```console
uv tool install a13n-harness-ui
cd your-repository
a13n-harness-ui
```

No repository checkout or Node.js is needed to use the published package. If the command is not on your PATH, run `uv tool update-shell` and restart your shell.

For a shorter command in Bash or Zsh, add this to `~/.bashrc` or `~/.zshrc`:

```bash
alias anui='a13n-harness-ui'
```

Reload that file or open a new shell, then use `anui`, `anui setup`, or `anui update`. The alias is optional; the installed executable remains `a13n-harness-ui`.

Update immediately without waiting for the startup check:

```console
a13n-harness-ui update
```

This explicitly runs `uv tool upgrade a13n-harness-ui` for the running tool installation, without opening chat or setup or asking for another confirmation. uv must be on PATH. Other installation methods receive manual package-manager guidance. Startup only checks for updates and asks before installing; it never installs automatically. Restart Harness UI after updating.

For source development, run `make a13n-harness-ui` from the repository root instead; it uses the uv workspace and skips release update checks. See [CONTRIBUTING.md](../../CONTRIBUTING.md) for the development toolchain.

## First Use

First use opens a single-screen setup wizard before chat: choose a connection, a model, and execution permissions. The final answer saves directly, without another confirmation. Existing compatible Codex/Grok logins are reused without another sign-in prompt. Reconfigure later with `a13n-harness-ui setup`, which returns to the command shell; chat has no `/setup`. `/help` explains chat commands; sign in outside chat with `a13n-harness-ui login codex`. Default Codex setup uses GPT-5.6 Sol, high reasoning, a 350k working context budget, and shell review. `setup --advanced` offers reasoning, 272k/350k/872k budgets, review, subagents, and additional instructions. `a13n-harness-ui add agent` creates another named agent without changing existing agents or defaults. Editable YAML contains actual values and thresholds, not opaque preset names.

`/mode concise|detailed` or Ctrl+O switches output live. Concise mode emphasizes text and necessary results; detailed mode includes exposed reasoning, file/tool calls, and bounded results. Display mode never changes model reasoning or tool permissions.

The current directory is the workspace. `/new` starts fresh without deleting history; `/resume` lists this directory's saved conversations. Internal Project and Thread identities are retained for persistence, not presented as a management workbench. `/agent` selects a complete configured agent for subsequent turns while retaining history; `/model` is its alias. `/thinking` adjusts reasoning without rewriting resources. The CLI and HTTP adapter reuse the same App boundary; the Hello World page does not call that API, and the CLI does not contain a second execution engine.

```console
a13n-harness-ui --environment-mode sandbox
a13n-harness-ui --resume session-id
a13n-harness-ui run "Review the current diff" --format json
a13n-harness-ui config path
a13n-harness-ui config validate
a13n-harness-ui login --help
```

Enter sends a prompt while idle or text guidance to the current Run while active; it never queues another Run or reorders input. Alt+Enter inserts a newline, Ctrl+C clears or cancels, and Ctrl+D on an empty draft exits. Bracketed multiline paste remains unsent until Enter. Rejected/busy commands preserve the draft. Cancellation waits for App-owned cleanup; nothing is approved implicitly or detached on exit.

See the [user guide](../../docs/a13n-harness-ui/index.md) for setup, all slash commands, explicit configuration examples and precedence, credentials, permissions, and recovery. The [interactive CLI contract](../../spec/a13n-harness-ui/07-interactive-cli.md) owns accepted terminal behavior.

## Configuration

Harness UI selects a root YAML from explicit `--config PATH` or the platform user path, `~/.a13n-harness-ui/a13n-harness-ui.yaml` on Unix-like systems. Fixed immediate sibling directories contain YAML Model, extension, MCP server, Agent, and Project resources, plus canonical Markdown `subagents/` definitions. MCP files also support `.json` and multi-server `mcpServers` objects, with literal or environment-referenced header/environment values. Direct editing remains a complete configuration path; valid changes reload without restarting imported Python code.

App-owned file mutations validate replacement content and use last-write-wins atomic publication. They require no expected source digest and do not reject concurrent editor saves. SQLite stores accepted-generation indexes and mutable Thread/runtime heads, but files remain desired-configuration authority. The data root is resolved before root-YAML parsing from `--data-root`, `A13N_HARNESS_UI_DATA_ROOT`, or the config directory's `data/` default.

Harness UI includes two fixed Environment modes. **Full Control** uses Direct Local Host execution. **Sandbox** shows the same canonical Host Project paths to the Agent but executes Project commands through Local Envd over EIP with required native isolation and denied networking.

### Automatic Model Prices

App lifetimes enable Pydantic AI's background price updates by default. Startup uses bundled prices immediately; successful downloads are adopted by subsequent root and async-child Agent builds. Existing Agents and usage records are not repriced. Download or catalog-conversion failure keeps the last valid prices, and no disk price cache is created.

Disable downloads in the selected root YAML and restart the App:

```yaml
schema_version: "2"
process:
  pricing_auto_update: false
```

This controls the updater owned by this App; it does not erase prices already downloaded by another updater in the same process. The setting does not change Agent model selection, context windows, or provider request parameters. See [Harness pricing](../../docs/a13n-harness/agents-and-runs.md#keep-prices-current-in-a-host) for embedded integrations and explicit bundled snapshots.

## Content Plugins

Declarative Content Plugins install Skills and canonical Markdown subagents from a Git repository without importing or executing plugin code. A repository contains `.agents/plugins/marketplace.yaml`; each indexed plugin contains `.a13n-plugin/plugin.yaml` and can point to `skills/` and `subagents/` directories.

```console
a13n-harness-ui plugin install https://github.com/example/agent-plugins.git
a13n-harness-ui plugin install https://github.com/example/agent-plugins.git --plugin plugin-reviewer --ref v1.0.0
a13n-harness-ui plugin list
a13n-harness-ui plugin uninstall plugin-reviewer
```

Install and list output includes the immutable installation directory so its complete content can be inspected directly. Uninstall removes the registration but retains that content-addressed directory for Runs that already captured it. Content Plugin management is an explicit CLI operation.

## Local Store Development

Harness UI owns its SQLite schema and Alembic history independently from a13n Service. Before the first published Harness UI release, an unreleased history may be squashed to one generated base revision because no supported user database depends on its revision IDs. After publication, retain revision identity and generate additive revisions. Generate every reviewed revision from the repository root against a disposable SQLite database:

```console
make a13n-harness-ui-db-migrate msg="describe the schema change"
```

The generator upgrades the disposable database to the current package head before comparing it with Harness UI metadata. Application startup only applies committed migrations; it never autogenerates against a user's data root.

## CLI Validation

Run `make check` and `make check-all` from the repository root. The CLI tests cover native PTY input, startup draft preservation, live display switching, cancellation, continuation recovery, and shell approval using isolated homes and deterministic test models. They do not require provider credentials.

The real Local Envd approval tests are opt-in because they require a matching native binary and working OS isolation. Build the repository binary, then run both Native and Sandbox approval/denial paths without a real model request:

```console
make rust-build
A13N_HARNESS_UI_TEST_SANDBOX=1 A13N_ENVD_EXECUTABLE="$PWD/target/debug/a13n-envd" \
  uv run --locked pytest packages/a13n-harness-ui/tests/test_interactive.py -k pending_shell
```

These tests inject the selected executable through App settings rather than downloading a release runtime. Unsupported isolation is a test failure when explicitly enabled; it never silently falls back to Full Control.

## Dependencies

The source manifest declares unversioned dependencies on `a13n-environment`, `a13n-harness`, and `a13n-stream-protocol`, so uv resolves all three from the workspace during repository development. Before tagging a Harness UI release, set `[tool.a13n.harness-ui-release].harness-version` to one published canonical Harness release such as `1.2.3` or `1.2.3-rc.1`; the `0.0.0` placeholder blocks a real release. Harness UI release automation pins all three dependencies to that exact normalized Python version before building publishable artifacts. Select a Harness release that depends on `a13n-environment`; releases using the former Environment distribution cannot be reused for the renamed UI.

## Packaging

Before tagging, set `a13n_harness_ui/assets/a13n-envd-version.txt` to one published canonical a13n-envd version. This single version is shared by source execution, wheels, and sdists; there is no duplicate selection in `pyproject.toml` or per-target hash/size manifest. The runtime derives the archive URL from the version and Host platform, downloads over HTTPS, extracts only the executable under a size limit, and checks `--version` before atomically caching it. Verify that release assets are anonymously accessible before publication. The cache is version-based, not content-addressed. The `0.0.0` placeholder blocks UI release preparation and automatic acquisition; an embedding App can supply an explicit executable through `HarnessUiSettings.envd_runtime.executable`.

The wheel and sdist contain the native CLI, reusable App, selected native version, and the project's Apache-2.0 license. They also include the WebUI server and compiled browser assets with a verified hash manifest. `make a13n-harness-ui-build` builds and bundles the private `apps/a13n-harness-ui` frontend. Node.js is needed only for repository/release asset preparation, not wheel installation, runtime, or wheel rebuilds from the sdist.

## Versioning

Harness UI releases independently through `release/a13n-harness-ui-v<version>`, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Its version does not need to match the selected Harness release; Python package metadata represents either RC as `X.Y.ZrcN`. The CLI has no companion npm artifact or independent frontend release.

The accepted architecture is defined in the [Harness UI specification](../../spec/a13n-harness-ui/README.md).

## Browser UI

The bundled page displays only **Hello World**. It does not authenticate, consume the URL's API-key fragment, open live streams, or provide conversation, setup, or management controls. The HTTP API and foreground server remain available independently.

```bash
a13n-harness-ui webui                       # 127.0.0.1:8765, generated per-process API key
a13n-harness-ui webui --host 127.0.0.1 --port 9000
```

Open the ordinary URL printed by the server to view the page; static assets require no API key. The server also prints a generated key and a convenience URL carrying it only in the fragment. The placeholder does not consume that fragment. API clients must send the key in `Authorization: Bearer <key>` for every API request. `--api-key` selects an explicit key; it is not echoed, but command arguments may be visible to the shell and operating system. `--dangerously-bypass-permission` disables authentication only by explicit request. A non-loopback listener is for a trusted single-user network, not a multi-user service. The server owns the App lifetime even when browsers disconnect; Ctrl+C stops the server and closes the App.

The browser assets ship inside the wheel. End users do not need Node.js or a separate frontend checkout. For repository development, run `make a13n-harness-ui-assets` before `uv run --locked a13n-harness-ui webui`.

## Windows Local Execution

Windows supports **Full Control only** for the built-in local modes. Setup and the CLI Environment selector offer Full Control and explain that commands run with the Host account's filesystem and network permissions. Job Object cleanup is not Sandbox isolation. Explicit Sandbox requests fail without downloading envd, changing saved selections, or falling back. Custom and remote Providers retain their own contracts.

## Source Environment Troubleshooting

After switching branches, run `make sync` (or launch with `make a13n-harness-ui`) to synchronize the locked workspace. This branch requires Pydantic AI 2.40 or newer; an older environment can fail with `cannot import name 'prices' from 'pydantic_ai'`. Do not work around this by importing upstream private modules. Installed users should upgrade `a13n-harness-ui` using the package manager that owns their environment.

### Terminal defaults

Install with `uv tool install a13n-harness-ui`; explicitly upgrade a uv-tool installation with `uv tool upgrade a13n-harness-ui`. Startup checks for updates before setup and presents a TUI confirmation; the check uses a daily cache and a short timeout. Set `process.terminal_update_check: false` or pass `--no-update-check` to disable detection. `make a13n-harness-ui` always disables it. Installation requires an explicit Update now answer each time, runs only after the App and TUI close, and ends with a restart instruction. Unrecognized installation methods receive manual guidance, never a guessed installer command.

Provider-exposed thinking and edit/multi-edit diffs are independently expanded. Summary and compaction blocks show their complete public payloads, including generated compaction summaries delivered through the native Capability event channel. Ctrl+O folds/expands retained ordinary tool details. Mouse scroll mode is enabled by default; Esc switches to native selection after closing any active interaction. Automatic themes preserve terminal colors. Diagnostics are rotated files under the data root, and clean exit prints a saved-session resume command. See the [Harness UI guide](../../docs/a13n-harness-ui/index.md) for keyboard controls and retention boundaries.
