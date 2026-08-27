# Agent UI

`a13n-ui` is the local single-user Host for Agent Foundation Harness. Its accepted interface selects WebUI by default and also exposes a TUI:

```console
a13n-ui
a13n-ui webui
a13n-ui tui
```

Both surfaces share one application service, local session authority, Harness execution path, and `a13n-stream-protocol` AG-UI projection.

The repository Make aliases preserve the same default and surface selection:

```console
make agent-ui
make agent-ui tui
```

`make agent-ui` invokes the package-provided `a13n-ui` command with no positional surface, selecting its default WebUI. Appending the `tui` goal forwards that positional argument and invokes `a13n-ui tui` exactly once. Asset preparation remains an independent build concern.

The repository directory is `packages/agent-ui`, the Python distribution is `a13n-ui`, and the import package is `a13n_ui`. The private browser source lives in [`apps/harness-ui`](../../apps/harness-ui/README.md).

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
