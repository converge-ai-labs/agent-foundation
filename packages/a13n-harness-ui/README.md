# Harness UI

`a13n-harness-ui` is the interactive coding CLI supplied by the `a13n-harness-ui` distribution. It uses a native full-terminal Markdown viewport, an editable multiline/image draft, and a compact status bar. One reusable `HarnessUiApp` owns execution, continuation-backed history, async subagents, decisions, and live events. `a13n-harness-ui webui` starts the HTTP API and bundled authentication/runtime-status page in a foreground server process; browser chat and management are not implemented. There is no detached daemon.

For custom interfaces, see [Python App embedding](../../docs/a13n-harness-ui/embedding.md) and [the HTTP API](../../docs/a13n-harness-ui/http-api.md). These preserve the distinction between process-local operation receipts, saved continuation, and best-effort observation.

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

The current directory is the workspace. `/new` starts fresh without deleting history; `/resume` opens a searchable, paginated browser with saved input/reply previews. Ctrl+T inspects a selected conversation without switching, F2 edits its name, and Ctrl+A toggles current/all-directory scope. Enter resumes; Escape preserves the original conversation and draft. Internal Project and Thread identities are retained for persistence, not presented as a management workbench. `/agent` selects a complete configured agent for subsequent turns while retaining history. `/model` selects and remembers a Model per Project without changing the Agent or YAML; `/model default` clears the preference and returns to the Agent's Model. Terminal restarts and resumed conversations use the launch Project's preference, while explicit launch `--agent` and noninteractive runs skip it. `/thinking` adjusts reasoning without rewriting resources. The CLI and HTTP adapter reuse the same App boundary; the browser foundation uses authenticated status queries, and the CLI does not contain a second execution engine.

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

Harness UI selects a root YAML from explicit `--config PATH` or the platform user path, `~/.a13n-harness-ui/a13n-harness-ui.yaml` on Unix-like systems. Fixed immediate sibling directories contain YAML Model, extension, MCP server, Agent, and Project resources, plus canonical Markdown `subagents/` definitions. MCP files also support `.json` and multi-server `mcpServers` objects, with literal or environment-referenced header/environment values. Direct editing remains a complete configuration path. Valid resource edits are accepted for later Run captures, not applied to an active Run; process settings and imported Python code require restart. See [configuration precedence and capture timing](../../docs/a13n-harness-ui/configuration.md#what-wins-and-when-edits-apply) for settings that apply live, to new Threads, or to subsequent Runs.

App-owned file mutations validate replacement content and use last-write-wins atomic publication. They require no expected source digest and do not reject concurrent editor saves. SQLite stores accepted-generation indexes and mutable Thread/runtime heads, but files remain desired-configuration authority. The data root is resolved before root-YAML parsing from `--data-root`, `A13N_HARNESS_UI_DATA_ROOT`, or the config directory's `data/` default.

Harness UI includes two fixed Environment modes. **Full Control** uses Direct Local Host execution. **Sandbox** shows the same canonical Host Project paths to the Agent but executes Project commands through Local Envd over EIP with required native isolation and denied networking.

### Automatic Model Prices

App lifetimes enable Pydantic AI's background price updates by default. Startup uses bundled prices immediately; successful downloads are adopted by subsequent root and async-child Agent builds. Existing Agents and usage records are not repriced. Download or catalog-conversion failure keeps the last valid prices, and no disk price cache is created.

Disable downloads in the selected root YAML and restart the App:

```yaml
schema_version: "1"
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

## Built-in Configuration Skill

Agents selecting the `skills` Capability automatically discover `harness-ui-configuration`. It supplies a configuration workflow, a generated documentation map, and a detailed section index with line ranges. Ask the Agent, for example, to use this Skill to inspect its configuration and explain how to enable an MCP server before changing anything.

The Skill and this release's Harness UI documentation are bundled in the wheel and sdist. They are exposed at `/environment/builtin-skills/harness-ui-configuration` through a read-only, file-only mount, including in projectless conversations and with Sandbox or remote Project profiles. No files are copied into your project or user Skill directory. Existing sources override the built-in Skill by name; omitting the Skills Capability omits the mount and catalog entry.

For source development, `make a13n-harness-ui-skills` regenerates the bundle from `mkdocs.yml`, `docs/a13n-harness-ui/`, and the package's operational template. `make a13n-harness-ui`, `make a13n-harness-ui-assets`, and `make test` prepare it before their consumers run. CI also generates it explicitly after dependency synchronization: cached editable wheels can skip the package build hook, so `uv sync` alone does not guarantee a current bundle. Package builds generate it; wheel rebuilds from the sdist use the bundled result without the repository docs. Generated content is ignored by Git. After editing documentation, regenerate before starting a new application process to inspect the updated Skill.

The Skill does not grant new configuration permissions or guarantee external connectivity. The Agent must distinguish accepted edits and warnings from later-Run changes, restart requirements, and actual model/MCP connection tests. Cross-topic references outside the bundled docs remain online references, listed in the section index.

## Local Store Development

Harness UI owns its SQLite schema and Alembic history independently from a13n Service. Before the first published Harness UI release, an unreleased history may be squashed to one generated base revision because no supported user database depends on its revision IDs. After publication, retain revision identity and generate additive revisions. Generate every reviewed revision from the repository root against a disposable SQLite database:

```console
make a13n-harness-ui-db-migrate msg="describe the schema change"
```

The generator upgrades the disposable database to the current package head before comparing it with Harness UI metadata. Application startup only applies committed migrations; it never autogenerates against a user's data root.

Keep automatic upgrades compatible with concurrently running older TUI/WebUI processes: preserve existing read/write meanings and allow older inserts to omit new fields. Test an active Run saving across migration and an older schema reader reconnecting after it. Startup does not require revision equality with the package head: an unknown newer single revision is left unchanged when the package's required tables and columns remain present. Missing required storage still fails explicitly. This structural check cannot prove payload or constraint compatibility; migration and serializer changes must preserve those semantics in development rather than forcing active users to restart. Already published older readers retain their original startup behavior. See [the storage compatibility contract](../../spec/a13n-harness-ui/03-local-storage-and-recovery.md#compatible-upgrades-across-app-versions).

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

The source manifest keeps project version `0.0.0` and unversioned workspace dependencies. `[tool.a13n.release-dependencies]` owns the bounded requirements injected into publishable artifacts: `>=0.1.0,<0.2.0` for Harness and Stream Protocol and for Logging, and `>=0.0.6,<0.1.0` for the Envd client. Keep the two Harness-group bounds identical; their own published dependencies retain exact group-version equality. Raise a lower bound only when consuming newer APIs or behavior, and explicitly review a move across a breaking compatibility line. See the [repository release policy](../../spec/repository-model.md#dependency-compatibility-lines).

## Packaging

Managed Local EIP acquisition reads the installed `a13n-envd-client` version through `importlib.metadata`; the client and native daemon are co-versioned. Stable versions retain their spelling, and Python RCs such as `0.0.5rc1` select canonical native `0.0.5-rc.1` releases. There is no native version text asset or latest-release lookup. The runtime derives the archive URL from the version and Host platform, downloads over HTTPS, extracts only the executable under a size limit, and checks `--version` before atomically caching it. Native release archives are publicly accessible before client publication. The cache is version-based, not content-addressed. Source version `0.0.0`, missing metadata, or invalid metadata fails only managed acquisition. Full Control remains available, and an embedding App can supply an explicit executable through `HarnessUiSettings.envd_runtime.executable`, subject to executable-version, isolation, and EIP compatibility validation.

The wheel and sdist contain the native CLI, reusable App, and the project's Apache-2.0 license. They also include the WebUI server and compiled browser assets with a verified hash manifest. `make a13n-harness-ui-build` builds and bundles the private `frontend/apps/a13n-harness-ui` frontend. Node.js is needed only for repository/release asset preparation, not wheel installation, runtime, or wheel rebuilds from the sdist.

## Versioning

Harness UI releases independently through `release/a13n-harness-ui-v<version>`, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Its version does not need to match its dependencies; Python package metadata represents an RC as `X.Y.ZrcN`. The release workflow publishes the Python artifacts and builds `ghcr.io/converge-ai-labs/a13n-harness-ui` from the same UI wheel. Stable images receive the release version and `latest` tags; RC images receive only the canonical RC tag. Application tests and local image smoke checks are not repeated during release. The CLI has no companion npm artifact or independent frontend release. For user upgrades and constraint handling, see [Install and update](https://agent-foundation-docs.converge.ai/a13n-harness-ui/#install-and-update).

The accepted architecture is defined in the [Harness UI specification](../../spec/a13n-harness-ui/README.md).

## Browser UI

The bundled foundation page accepts an API key, consumes and removes the convenience URL's key fragment, and displays the installed Python package version returned by `/api/status`. It retains successfully used keys in same-origin localStorage, with an explicit Forget API key action. It does not yet provide conversation, setup, shared drafts, Host Files, Git, or terminal controls. The HTTP API is available independently: WebUI enables complete native Host Files operations as the server OS account by default; `--no-share-computer` opts out. `features.host_files` reports that App gate; `features.host_git` additionally requires an installed Git executable. Read-only [Git Changes](../../docs/a13n-harness-ui/http-api.md#native-git-changes) provides repository/worktree discovery, staged/unstaged status and diffs, and captured comparison input. PTY, shared drafts, and browser workbench panels remain unavailable. See the [Files API](../../docs/a13n-harness-ui/http-api.md#native-host-files) for revision conflicts, bounded transfers, deliberate deletion, and captured Thread input.

```bash
a13n-harness-ui webui                       # 127.0.0.1:8765, generated per-process API key
a13n-harness-ui webui --host 127.0.0.1 --port 9000
a13n-harness-ui webui --no-share-computer   # Disable native Files and Git APIs
```

Open the URL printed on startup stdout; static assets require no API key. Key precedence is `--apikey`, then `A13N_HARNESS_UI_API_KEY`, then a freshly generated process key. Only generated keys appear in startup stdout, together with a convenience fragment URL; supplied keys are never echoed. API clients send `Authorization: Bearer <key>` on every API request. Command-line keys may be visible to the shell and operating system. `--dangerous-skip-permissions` disables Web authentication only, not Agent permissions; combining it with a CLI or environment key is an error. `--api-key` and `--dangerously-bypass-permission` remain aliases. Conflicting repeated key values and explicitly empty keys are rejected. A non-loopback listener grants shared instance authority on a trusted network, not tenant isolation; use external TLS when needed. The server owns the App lifetime even when browsers disconnect; Ctrl+C or SIGTERM stops the server and closes the App. `/healthz` reports process liveness and `/readyz` reports App readiness without requiring credentials or revealing configuration. Missing model configuration does not block readiness for setup.

The browser assets ship inside the wheel. End users do not need Node.js or a separate frontend checkout. For repository development, run `make a13n-harness-ui-assets` before `uv run --locked a13n-harness-ui webui`.

### Docker

The initial non-root image packages the application, Python, Bash, Git, curl, and the system CA store. The default container command enables native sharing and exposes the container's Files and Git APIs and mounted paths, not an Agent Environment. Override it with `webui --host 0.0.0.0 --no-share-computer` to disable sharing; native PTY and browser panels remain unavailable. The full development-image target is described in the [distribution specification](../../spec/a13n-harness-ui/webui/03-distribution.md). There is no Node.js runtime requirement, privileged mode, Docker socket, or separate service/database prerequisite.

```bash
# Published image, loopback port, and named persistent config/data/work volumes:
docker compose -f deploy/compose/a13n-harness-ui.yaml up -d
docker compose -f deploy/compose/a13n-harness-ui.yaml logs harness-ui

# On-demand source build and local run:
make image-a13n-harness-ui
make image-check-a13n-harness-ui
A13N_HARNESS_UI_IMAGE=a13n-harness-ui:local \
  docker compose -f deploy/compose/a13n-harness-ui.yaml up -d
```

Use `A13N_HARNESS_UI_IMAGE=ghcr.io/converge-ai-labs/a13n-harness-ui:X.Y.Z` for a release rather than the mutable `dev` default. Development images show source package version `0.0.0` and their Git revision separately; RC package metadata displays `X.Y.ZrcN`. The private npm version is never used as the running application version. Main builds publish only `dev`; release builds publish the canonical version, plus `latest` for stable releases only. Overwriting `dev` does not delete old registry digests; automatic cleanup is not configured.

The Compose file persists configuration under `/home/app/.a13n-harness-ui`, App data under `/data`, and the working directory under `/work`. Bind-mounted directories must be writable by UID/GID `10001:10001`; mounting a directory deliberately exposes it to the container account. Named volumes are initialized with the image's ownership. Do not use `down --volumes` when preserving data. Changing a container does not restore live Runs or terminals. With no supplied key, restart rotates the key; obtain the new convenience URL from container startup output. Keep that output private. Supply `A13N_HARNESS_UI_API_KEY` at runtime for a stable key, never as a build argument.

Local image checks should cover non-root startup, protected API access, runtime version, persistent config/data/work across replacement, and graceful SIGTERM. They are on-demand checks, not recurring WebUI CI jobs. The image uses Tini to forward signals and reap children; its default port is `8765`.

## Windows Local Execution

Windows supports **Full Control only** for the built-in local modes. Setup and the CLI Environment selector offer Full Control and explain that commands run with the Host account's filesystem and network permissions. Job Object cleanup is not Sandbox isolation. Explicit Sandbox requests fail without downloading envd, changing saved selections, or falling back. Custom and remote Providers retain their own contracts.

## Source Environment Troubleshooting

After switching branches, run `make sync` (or launch with `make a13n-harness-ui`) to synchronize the locked workspace. This branch requires Pydantic AI 2.40 or newer; an older environment can fail with `cannot import name 'prices' from 'pydantic_ai'`. Do not work around this by importing upstream private modules. Installed users should upgrade `a13n-harness-ui` using the package manager that owns their environment.

### Terminal defaults

Install with `uv tool install a13n-harness-ui`; explicitly upgrade a uv-tool installation with `uv tool upgrade a13n-harness-ui`. Startup checks for updates before setup and presents a TUI confirmation; the check uses a daily cache and a short timeout. Set `process.terminal_update_check: false` or pass `--no-update-check` to disable detection. `make a13n-harness-ui` always disables it. Installation requires an explicit Update now answer each time, runs only after the App and TUI close, and ends with a restart instruction. Unrecognized installation methods receive manual guidance, never a guessed installer command.

Provider-exposed thinking and edit/multi-edit diffs are independently expanded. Summary and compaction blocks show their complete public payloads, including generated compaction summaries delivered through the native Capability event channel. Ctrl+O folds/expands retained ordinary tool details. Mouse scroll mode is enabled by default; Esc switches to native selection after closing any active interaction. Automatic themes preserve terminal colors. Diagnostics are rotated files under the data root, and clean exit prints a saved-session resume command. See the [Harness UI guide](../../docs/a13n-harness-ui/index.md) for keyboard controls and retention boundaries.

Usage reports retain schema version `1`. The optional model `call_id` correlates a native model-handler invocation with its committed usage; it defaults to `None` for records without known dispatch correlation. `None` does not prove that no dispatch occurred. Existing version 1 reports and stored records without the field remain readable without a usage migration. The Harness/Stream Protocol `>=0.1.0,<0.2.0` dependency line selects the required package APIs independently of the usage schema version.
