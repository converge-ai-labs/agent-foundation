import pytest
from a13n_harness.providers.plugins import load_provider_plugins


@pytest.mark.anyio
async def test_installed_workspace_direct_operations_and_reuse(tmp_path):
    """The Host creates the directory before either execution is opened."""
    definition = load_provider_plugins(("acme",))[0].manifest.environment[0]
    recipe = {"directory": "notes"}
    (tmp_path / "notes" / "env-notes").mkdir(parents=True)
    connector = definition.execution_connector(
        recipe, configuration={"root": str(tmp_path)}, environment_id="env-notes"
    )
    async with await connector.open() as first:
        assert first.operations.files is not None
        await first.operations.files.write_text("/hello.txt", "persistent", mode="create")
    async with await connector.open() as second:
        assert second.execution_id != first.execution_id
        assert second.operations.files is not None
        assert (await second.operations.files.read_text("/hello.txt")).text == "persistent"
