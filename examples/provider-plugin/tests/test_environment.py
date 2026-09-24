import pytest
from a13n_harness.providers.plugins import load_provider_plugins


def _files(environment):
    operations = environment.operations.files
    assert operations is not None, "the prepared workspace must expose file operations"
    return operations


@pytest.mark.anyio
async def test_installed_workspace_direct_operations_and_reuse(tmp_path):
    """One installed plugin performs real operations without an Agent Run."""
    definition = load_provider_plugins(("acme",))[0].manifest.environment[0]
    first = await definition.create({"directory": "notes"}, configuration={"root": str(tmp_path)})
    await first.prepare()  # Provisioning before binding is supported.
    async with first:
        await _files(first).write_text("/hello.txt", "persistent", mode="create")
    second = await definition.create(
        {"directory": "notes"},
        configuration={"root": str(tmp_path)},
        environment_id=first.environment_id,
        allow_create=False,
    )
    async with second:
        await second.prepare()
        assert (await _files(second).read_text("/hello.txt")).text == "persistent"
