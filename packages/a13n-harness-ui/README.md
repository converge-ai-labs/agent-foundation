# Harness UI

Harness UI is a TUI and WebUI workbench built on [Harness](../a13n-harness/README.md). It is for individuals and trusted small teams working with Agents, Models, tools, Skills, and local projects. Both interfaces use one process-local `HarnessUiApp`; saved conversations support continuation, but active work is not a durable job queue. For managed access and worker recovery, use [Service](../a13n-service/README.md).

## Install and Run

Install the published application with [uv](https://docs.astral.sh/uv/getting-started/installation/):

```console
uv tool install a13n-harness-ui
cd your-repository
a13n-harness-ui
```

For WebUI, run `a13n-harness-ui webui` and open the printed login link. Neither installed interface requires a checkout or Node.js. If the command is not on PATH, run `uv tool update-shell` and reopen your shell.

Upgrade a uv-tool installation with `a13n-harness-ui update` or `uv tool upgrade a13n-harness-ui`, then restart it. Use the original package manager for other installations. For source development, run `make a13n-harness-ui` from the repository root; see [CONTRIBUTING.md](../../CONTRIBUTING.md).

## First Use

First launch guides you through a Model connection, model selection, and execution permissions. Setup saves editable files and opens chat; no model call occurs until you send a prompt. To rerun setup later, use `a13n-harness-ui setup` (outside chat). Run `a13n-harness-ui setup --advanced` for optional context, reasoning, shell-review, subagent, and instruction choices. To add another resource, use `a13n-harness-ui add model` or `a13n-harness-ui add agent`.

Full Control runs commands as your host account. Sandbox requires supported local isolation and never silently falls back; built-in Windows execution is Full Control only. Start with a read-only prompt if you are exploring a repository:

```text
Explain the entry point and the relevant tests. Do not change files.
```

Inside chat, `/help` lists actions, `/agent` changes the complete Agent, `/model` selects a Model, `/resume` opens saved conversations, and `/environment` changes execution permissions. For a one-shot task (which cannot answer an interactive approval), run:

```console
a13n-harness-ui run "Review the current diff" --format json
```

See the [user guide](../../docs/a13n-harness-ui/index.md), [terminal workflow](../../docs/a13n-harness-ui/everyday-use.md), and [CLI command reference](../../docs/a13n-harness-ui/command-reference.md).

## Configuration

Use `a13n-harness-ui config path`, then edit the selected root YAML and its sibling `models/`, `agents/`, `projects/`, `mcp/`, and `extensions/` directories. The default root on Unix-like systems is `~/.a13n-harness-ui/a13n-harness-ui.yaml`. An explicit `--config PATH` selects a separate tree; `--data-root PATH` selects its data store. Validate edits with `a13n-harness-ui config validate`. Valid edits affect subsequent Runs, not a Run already in progress. See [configuration recipes](../../docs/a13n-harness-ui/configuration-recipes.md) and [precedence](../../docs/a13n-harness-ui/configuration.md#what-wins-and-when-edits-apply).

### Automatic Model Prices

The App starts with bundled prices and can update them for later Agent builds. To disable background downloads, set `process.pricing_auto_update: false` in the root YAML and restart. This does not alter existing usage records or provider prices. See [Harness pricing](../../docs/a13n-harness/usage-and-limits.md#keep-prices-current-in-a-host).

## Content Plugins

Content Plugins distribute Skills and Markdown subagents from a Git repository; they do not execute plugin Python code. Inspect and trust the source before installing it:

```console
a13n-harness-ui plugin install /path/to/plugin-repository
a13n-harness-ui plugin list
a13n-harness-ui plugin uninstall plugin-reviewer
```

Uninstall deletes the installed content, including local edits. For the marketplace layout, registration and selection rules, see [Skills and Content Plugins](../../docs/a13n-harness-ui/skills-and-content-plugins.md).

## Built-in Configuration Skill

An Agent with the `skills` Capability discovers the bundled `harness-ui-configuration` Skill under `/environment/builtin-skills/harness-ui-configuration`. It contains release-matched configuration guidance and a generated documentation index; it does not grant extra permissions. The read-only mount also works in projectless and Sandbox conversations.

Source development regenerates the bundle with `make a13n-harness-ui-skills` after changes to `docs/a13n-harness-ui/` or navigation. Generated output is not the source of truth. The build and launch targets prepare it; `uv sync` alone may leave an editable installation's bundle stale. See [bundled Skill](../../docs/a13n-harness-ui/skills-and-content-plugins.md#built-in-configuration-skill).

## Local Store Development

Harness UI owns its SQLite schema separately from Service. Generate reviewed revisions against a disposable database from the repository root:

```console
make a13n-harness-ui-db-migrate msg="describe the schema change"
```

Application startup applies committed migrations, never autogenerates against user data. Storage changes must preserve compatible readers and writes when older processes share the data root; see the [storage compatibility contract](../../spec/a13n-harness-ui/03-local-storage-and-recovery.md#compatible-upgrades-across-app-versions).

## CLI Validation

Select focused checks using [Local validation](../../CONTRIBUTING.md#local-validation). CLI tests use isolated homes and deterministic models; they need no provider account. Real Local Envd approval tests are opt-in and need a matching native binary and working isolation:

```console
make rust-build
A13N_HARNESS_UI_TEST_SANDBOX=1 A13N_ENVD_EXECUTABLE="$PWD/target/debug/a13n-envd" \
  uv run --locked pytest packages/a13n-harness-ui/tests/test_interactive_run_control.py -k pending_shell
```

## Dependencies

The source version is `0.0.0` with unversioned workspace dependencies. Publishable bounds live in `[tool.a13n.release-dependencies]`; Harness UI releases independently from Harness and Stream Protocol. Check the manifest and [release policy](../../spec/repository-model.md#dependency-compatibility-lines) before changing bounds.

## Packaging

The wheel and sdist include the CLI, App, WebUI server, compiled WebUI assets, configuration Skill and license; installed users need no Node.js. `make a13n-harness-ui-build` builds the private browser frontend for release preparation. Sandbox can lazily acquire a native Envd matching the installed client version; Full Control does not need Envd. See [installation](../../docs/a13n-harness-ui/installation.md#sandbox-runtime).

## Versioning

The release tag is `release/a13n-harness-ui-v<version>`. Harness UI and its container image share the application version; the frontend has no separate release. See the [release policy](../../spec/repository-model.md) and [installation/upgrade guide](../../docs/a13n-harness-ui/installation.md). The [Harness UI specification](../../spec/a13n-harness-ui/README.md) owns the architecture.

## Browser UI

```console
a13n-harness-ui webui
a13n-harness-ui webui --host 127.0.0.1 --port 9000
a13n-harness-ui webui --no-share-computer
```

WebUI serves shared conversations, live output, approvals, setup, configuration, and native Files/Git/terminal panels. Native computer access is enabled by default and operates as the server account, independently of Agent permissions; disable it with `--no-share-computer`. Participants share instance authority rather than separate authenticated identities. Keep the server running for active work. For access-key options and browser workflows, see [Use WebUI](../../docs/a13n-harness-ui/webui.md); for integration, see the [HTTP API](../../docs/a13n-harness-ui/http-api.md).

### Docker

The development image packages the same WebUI. The default container command exposes the container account's files, terminals, and mounted paths; select `--no-share-computer` if that is not wanted.

```console
docker compose -f deploy/docker/compose/a13n-harness-ui.yaml up -d
docker compose -f deploy/docker/compose/a13n-harness-ui.yaml logs harness-ui
```

The Compose file uses persistent configuration, data, and work volumes. Bind-mounted directories must be writable by UID/GID `10001:10001`. Keep startup output private (it may contain a generated login link) and do not run `down --volumes` to preserve data. See the [distribution specification](../../spec/a13n-harness-ui/webui/03-distribution.md) for image build and deployment details.

## Windows Local Execution

Built-in Windows execution supports Full Control, not Sandbox. Explicit Sandbox selection fails rather than running with host authority; custom and remote Providers have their own requirements. See [execution permissions](../../docs/a13n-harness-ui/environments-and-projects.md#execution-permissions).

## Source Environment Troubleshooting

After switching source branches, run `make sync` or `make a13n-harness-ui` to use the checkout's locked dependencies. Installed users should upgrade through the package manager that owns their installation.

### TUI defaults

Startup can offer an update; it does not install one without confirmation. Use `--no-update-check` or `process.terminal_update_check: false` to disable the check. Run `a13n-harness-ui update` to request an immediate upgrade. For TUI controls, diagnostics, and recovery, see [Use the TUI](../../docs/a13n-harness-ui/everyday-use.md) and [Troubleshooting](../../docs/a13n-harness-ui/automation-and-troubleshooting.md).
