# Agent UI

`a13n-ui` is the local single-user Host for Agent Foundation Harness. Its current executable path is an interactive Codex-style CLI with one-shot runtime commands:

```console
a13n-ui
a13n-ui runtime status
```

The CLI opens one stable `AgentUiHost` with local Session authority and runtime Runner supervision. Restart starts a fresh Runner, validates and promotes it, then drains the previous Runner without restarting the Host or terminal frontend. The bundled WebUI remains a peer surface in the accepted architecture, but its product interaction design is not coupled to this CLI foundation.

The repository Make alias starts the interactive CLI:

```console
make agent-ui
```

Asset preparation remains an independent build concern.

The repository directory is `packages/agent-ui`, the Python distribution is `a13n-ui`, and the import package is `a13n_ui`. The private browser source lives in [`apps/harness-ui`](../../apps/harness-ui/README.md).

## YAML Configuration

Agent UI selects one full process-settings YAML from explicit `--config PATH` or `~/.a13n-ui/settings.yaml`. It does not merge profiles, scan the current project, or apply implicit environment overlays. When the default file is absent, built-in defaults select:

```text
~/.a13n-ui/data
~/.a13n-ui/definitions/{models,prompts,plugins,skill-sources,skills,agents,environments}
```

Model, Prompt, Agent, Environment, and other product definitions remain separate strict YAML/JSON files under the configured definition roots. SQLite indexes accepted generations but is not configuration authority.

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
