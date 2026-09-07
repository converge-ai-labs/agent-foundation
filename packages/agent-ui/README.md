# Agent CLI

`a13n-ui` is the interactive coding CLI supplied by the `a13n-ui` distribution. It uses a native full-terminal Markdown viewport, an editable multiline/image draft, and a compact status bar. One reusable `AgentUiApp` owns execution, continuation-backed history, async subagents, decisions, and live events. `a13n-ui webui` starts the bundled browser interface in a foreground server process. There is no detached daemon.

```console
cd your-repository
a13n-ui
# From a source checkout:
make a13n-ui
# From a published distribution:
uvx --from a13n-ui a13n-ui
```

The prompt is editable before heavy runtime imports finish. `/help` explains commands, `/setup` configures access and permissions, and `/login codex` starts device authorization. Default Codex setup uses GPT-5.6 Sol, high reasoning, and an explicit 350k working context budget. Setup also offers 272k and 872k budgets with explanations; editable YAML contains actual values and thresholds, not opaque preset names.

`/mode concise|detailed` or Ctrl+O switches output live. Concise mode emphasizes text and necessary results; detailed mode includes exposed reasoning, file/tool calls, and bounded results. Display mode never changes model reasoning or tool permissions.

The current directory is the workspace. `/new` starts fresh without deleting history; `/resume` lists this directory's saved conversations. Internal Project and Thread identities are retained for persistence, not presented as a management workbench. `/model` and `/thinking` affect subsequent Run captures without rewriting resources. The CLI and WebUI reuse the same App boundary; the CLI does not contain a second execution engine.

```console
a13n-ui --environment-mode sandbox
a13n-ui --resume session-id
a13n-ui run "Review the current diff" --format json
a13n-ui config path
a13n-ui config validate
a13n-ui auth login --help
```

Enter submits, Alt+Enter inserts a newline, Ctrl+C clears or cancels, and Ctrl+D on an empty draft exits. Bracketed multiline paste remains unsent until Enter. Rejected/busy commands preserve the draft. Cancellation waits for App-owned cleanup; nothing is approved implicitly or detached on exit.

See the [user guide](../../docs/agent-cli.md) for setup, all slash commands, explicit configuration examples and precedence, credentials, permissions, and recovery. The [interactive CLI contract](../../spec/agent-cli/07-interactive-cli.md) owns accepted terminal behavior.

## Configuration

Agent UI selects a root YAML from explicit `--config PATH` or the platform user path, `~/.a13n-ui/a13n-ui.yaml` on Unix-like systems. Fixed immediate sibling directories contain one YAML resource per Model, extension, MCP server, Agent, or Project, plus one canonical Markdown file per `subagents/` definition. Direct editing remains a complete configuration path; valid changes reload without restarting imported Python code.

App-owned file mutations require expected source digests and reject stale writes. SQLite stores accepted-generation indexes and mutable Thread/runtime heads, but files remain desired-configuration authority. The data root is resolved before root-YAML parsing from `--data-root`, `A13N_UI_DATA_ROOT`, or the config directory's `data/` default.

Agent UI includes two fixed Environment modes. **Full Control** uses Direct Local Host execution. **Sandbox** shows the same canonical Host Project paths to the Agent but executes Project commands through Local Envd over EIP with required native isolation and denied networking.

### Automatic Model Prices

App lifetimes enable Pydantic AI's background price updates by default. Startup uses bundled prices immediately; successful downloads are adopted by subsequent root and async-child Agent builds. Existing Agents and usage records are not repriced. Download or catalog-conversion failure keeps the last valid prices, and no disk price cache is created.

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

Install and list output includes the immutable installation directory so its complete content can be inspected directly. Uninstall removes the registration but retains that content-addressed directory for Runs that already captured it. Content Plugin management is an explicit CLI operation.

## Local Store Development

Agent UI owns its SQLite schema and Alembic history independently from Foundation Service. Before the first published Agent UI release, an unreleased history may be squashed to one generated base revision because no supported user database depends on its revision IDs. After publication, retain revision identity and generate additive revisions. Generate every reviewed revision from the repository root against a disposable SQLite database:

```console
make agent-ui-db-migrate msg="describe the schema change"
```

The generator upgrades the disposable database to the current package head before comparing it with Agent UI metadata. Application startup only applies committed migrations; it never autogenerates against a user's data root.

## CLI Validation

Run `make check` and `make check-all` from the repository root. The CLI tests cover native PTY input, startup draft preservation, live display switching, cancellation, continuation recovery, and shell approval using isolated homes and deterministic test models. They do not require provider credentials.

The real Local Envd approval tests are opt-in because they require a matching native binary and working OS isolation. Build the repository binary, then run both Native and Sandbox approval/denial paths without a real model request:

```console
make rust-build
A13N_UI_TEST_SANDBOX=1 A13N_AGENT_ENVD_EXECUTABLE="$PWD/target/debug/agent-envd" \
  uv run --locked pytest packages/agent-ui/tests/test_interactive.py -k pending_shell
```

These tests inject the selected executable through App settings rather than downloading a release runtime. Unsupported isolation is a test failure when explicitly enabled; it never silently falls back to Full Control.

## Dependencies

The source manifest declares unversioned dependencies on `a13n-environment-provider`, `a13n-harness`, and `a13n-stream-protocol`, so uv resolves all three from the workspace during repository development. Before tagging an Agent UI release, set `[tool.a13n.agent-ui-release].harness-version` to one published canonical Harness release such as `1.2.3` or `1.2.3-rc.1`; the `0.0.0` placeholder blocks a real release. Agent UI release automation pins all three dependencies to that exact normalized Python version before building publishable artifacts.

## Packaging

The wheel and sdist contain the native CLI, reusable App, runtime release manifest, and the YAACLI BSD attribution for adapted presentation components. They also include the WebUI server and compiled browser assets with a verified hash manifest. `make agent-ui-build` builds and bundles the private `apps/harness-ui` frontend. Node.js is needed only for repository/release asset preparation, not wheel installation, runtime, or wheel rebuilds from the sdist.

## Versioning

Agent UI releases independently through `release/agent-ui-v<version>`, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Its version does not need to match the selected Harness release; Python package metadata represents either RC as `X.Y.ZrcN`. The CLI has no companion npm artifact or independent frontend release.

The accepted architecture is defined in the [Agent CLI specification](../../spec/agent-cli/README.md).

## Browser UI

```bash
a13n-ui webui                       # 127.0.0.1:8765, generated per-process API key
a13n-ui webui --host 127.0.0.1 --port 9000
```

Open the URL printed by the server. The generated key is carried only in the URL fragment and is required for every API request. `--api-key` selects an explicit key; it is not echoed, but command arguments may be visible to the shell and operating system. `--dangerously-bypass-permission` disables authentication only by explicit request. A non-loopback listener is for a trusted single-user network, not a multi-user service. The server owns the App lifetime even when browsers disconnect; Ctrl+C stops the server and closes the App.

The browser assets ship inside the wheel. End users do not need Node.js or a separate frontend checkout. For repository development, run `make agent-ui-assets` before `uv run --locked a13n-ui webui`.

## Windows Local Execution

Windows supports **Full Control only** for the built-in local modes. Setup and the CLI Environment selector offer Full Control and explain that commands run with the Host account's filesystem and network permissions. Job Object cleanup is not Sandbox isolation. Explicit Sandbox requests fail without downloading envd, changing saved selections, or falling back. Custom and remote Providers retain their own contracts.

## Source Environment Troubleshooting

After switching branches, run `make sync` (or launch with `make a13n-ui`) to synchronize the locked workspace. This branch requires Pydantic AI 2.40 or newer; an older environment can fail with `cannot import name 'prices' from 'pydantic_ai'`. Do not work around this by importing upstream private modules. Installed users should upgrade `a13n-ui` using the package manager that owns their environment.
