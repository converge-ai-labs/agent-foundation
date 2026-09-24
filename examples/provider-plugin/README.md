# Installed Environment Provider plugin example

This independent package depends only on Harness and Pydantic. It exports an immutable `ProviderManifest` with one Environment definition, `acme_workspace`, through the `a13n_harness.providers.plugins` entry-point group. Installation makes it discoverable; a Host must explicitly select `acme` to load it. Selecting it imports the definition without Agent orchestration or optional sandbox SDKs.

`acme_workspace` is a project workspace backed by Direct Local operations. Its account configuration names an absolute `root`; each Environment configuration selects a `directory` beneath it, and each Environment owns `<root>/<directory>/<environment_id>`.

## Direct use

The installed manifest and a direct import share one definition. Harness validates both configurations, runs the definition's `runtime_factory`, and constructs a fresh adapter:

```python
from a13n_harness.providers.plugins import load_provider_plugins

definition = load_provider_plugins(("acme",))[0].manifest.environment[0]
workspace = await definition.create({"directory": "notes"}, configuration={"root": "/srv/acme"})
async with workspace:
    await workspace.operations.files.write_text("/hello.txt", "persistent", mode="create")

again = await definition.create(
    {"directory": "notes"},
    configuration={"root": "/srv/acme"},
    environment_id=workspace.environment_id,
    allow_create=False,
)
```

`allow_create=False` reuses the existing workspace and refuses to create a missing one. Leaving the adapter context is non-destructive; the workspace directory remains for the next adapter with the same `environment_id`.

## Harness UI loading

An embedding Host selects installed plugins by entry-point name in `HarnessUiSettings(provider_plugins=("acme",))`. Harness UI lists `acme_workspace` as an installed Environment Provider. An [Environment profile](../../docs/a13n-harness-ui/environments-and-projects.md#custom-environment-profiles) can select it once an installed Project adapter supports that Provider type. Selection makes trusted code available; it grants no runtime authority by itself.

## Run the tests

```bash
uv sync --project examples/provider-plugin --locked
uv run --project examples/provider-plugin --locked pytest examples/provider-plugin/tests
```

The tests load the installed entry point, check that loading stays inert, and perform real file operations in a temporary directory.
