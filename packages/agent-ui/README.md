# Agent UI

`a13n-ui` is a local single-user workstation for Agent Foundation Harness. CLI and Web adapters share one process-local `AgentUiApp`; Harness execution, mutable continuation-backed Threads, async subagents, and live presentation run in that process. Agent UI does not start or supervise a replaceable Runner process and does not hot-reload imported Python extension code.

The CLI starts with either command:

```console
a13n-ui
a13n-ui cli
```

A one-shot Run can use the same human-editable configuration tree:

```console
a13n-ui --config ~/.a13n-ui/a13n-ui.yaml run "Inspect the Agent behavior"
```

Projects are the only local-root grouping concept. Every root or child Thread owns sticky mutable selections, while each admitted Run captures an immutable resolved composition. CLI, WebUI, and model-visible Thread tools call the same application boundary. The exact accepted behavior and migration target are owned by the [Agent UI specification](../../spec/agent-ui/README.md).

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

Agent UI selects a root YAML from explicit `--config PATH` or the platform user path, `~/.a13n-ui/a13n-ui.yaml` on Unix-like systems. Fixed immediate sibling directories contain one YAML resource per Model, extension, MCP server, Agent, or Project, plus one canonical Markdown file per `subagents/` definition. Direct editing remains a complete configuration path; valid changes reload without restarting imported Python code.

CLI and WebUI file mutations require expected source digests and reject stale writes. SQLite stores accepted-generation indexes and mutable Thread/runtime heads, but files remain desired-configuration authority. The data root is resolved before root-YAML parsing from `--data-root`, `A13N_UI_DATA_ROOT`, or the config directory's `data/` default.

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
