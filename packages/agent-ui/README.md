# Agent UI

`a13n-ui` is a local single-user workstation for Agent Foundation Harness. CLI and Web adapters share one process-local `AgentUiApp`; Harness execution, async subagents, and live presentation run in that process. Agent UI does not start or supervise a replaceable Runner process and does not hot-reload imported Plugin code.

The interactive CLI starts with either command:

```console
a13n-ui
a13n-ui cli
```

Run one message in a fresh process for automation or Plugin debugging:

```console
a13n-ui --config ~/.a13n-ui/agent-ui.yaml run "Inspect the Plugin behavior"
a13n-ui --config ~/.a13n-ui/agent-ui.yaml run "Inspect it again" --session session-... --format json
```

A new one-shot Session uses `defaults.agent` and the selected Agent or global default Environment. `--agent`, `--environment`, repeated `--folder`, and `--title` override creation inputs. An existing `--session` continues its pinned snapshots. Workspace binding defaults to the current directory, and output is bounded human-readable text or JSON.

The completed command family also provides Session management, configuration migration, and the browser surface. Plugin authors debug in a fresh headless process or call the Harness library directly rather than rotating code inside a running App.

The repository Make alias starts the interactive CLI:

```console
make a13n-ui
```

After publication, the distribution and console entrypoint share the same name:

```console
uvx a13n-ui
uvx a13n-ui cli
```

The repository directory is `packages/agent-ui`, the Python distribution is `a13n-ui`, and the import package is `a13n_ui`. The private browser source lives in [`apps/harness-ui`](../../apps/harness-ui/README.md).

## Configuration

Agent UI selects one strict YAML document from explicit `--config PATH` or the platform user path, `~/.a13n-ui/agent-ui.yaml` on Unix-like systems. It does not merge profiles, scan the current project, or walk parent directories. Canonical immediate sibling `subagents/*.md` files are the only live Markdown subagent source.

SQLite indexes accepted snapshots, Sessions, child Threads, and Environment state, but it is not desired-configuration authority. Existing Sessions retain pinned snapshots after configuration reload.

## Local Store Development

Agent UI owns its SQLite schema and Alembic history independently from Foundation Service. Generate a reviewed revision from the repository root against a disposable SQLite database:

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
