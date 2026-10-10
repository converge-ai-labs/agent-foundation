# Installed Environment Provider plugin example

This independent package depends on Environment, Harness plugin discovery, and Pydantic. It exports an immutable `ProviderManifest` with one Environment definition, `acme_workspace`, through the `a13n_harness.providers.plugins` entry-point group. Installation makes it discoverable; a Host must explicitly select `acme` to load it. Selecting it imports the definition without Agent orchestration or optional sandbox SDKs.

`acme_workspace` is a project workspace backed by Direct Local operations. Its account configuration names an absolute `root`; each Environment configuration selects a `directory` beneath it, and each Environment owns `<root>/<directory>/<environment_id>`.

## Direct use

The installed manifest and a direct import share one definition. Construction validates configuration without performing I/O. Management explicitly creates the workspace; execution only opens an existing directory.

```python
from a13n_harness.providers.plugins import load_provider_plugins

definition = load_provider_plugins(("acme",))[0].manifest.environment[0]
recipe = {"directory": "notes"}
provider = await definition.open_provider(configuration={"root": "/srv/acme"})
try:
    state = await provider.create(recipe, environment_id="env_notes", operation_id="op_create")
    connector = provider.execution_connector(recipe, environment_id="env_notes", state=state)
finally:
    await provider.close()

async with await connector.open() as execution:
    await execution.operations.files.write_text("/hello.txt", "persistent", mode="create")
async with await connector.open() as execution:
    assert (await execution.operations.files.read_text("/hello.txt")).text == "persistent"
```

Closing the management provider does not invalidate the connector. Execution close preserves the directory. This sample provider does not support stop, destruction, or renewal; those actions belong to its Host.

## Harness UI loading

An embedding Host selects installed plugins by entry-point name in `HarnessUiSettings(provider_plugins=("acme",))`. Harness UI lists `acme_workspace` as an installed Environment Provider. An [Environment profile](../../docs/a13n-harness-ui/environments-and-projects.md#custom-environment-profiles) can select it once an installed Project adapter supports that Provider type. Selection makes trusted code available; it grants no runtime authority by itself.

## Run the tests

```bash
uv sync --project examples/provider-plugin --locked
uv run --project examples/provider-plugin --locked pytest examples/provider-plugin/tests
```

The tests load the installed entry point, check that loading stays inert, and perform real file operations in a temporary directory.
