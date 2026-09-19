import pytest
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.memory.builtins import FILESYSTEM, MEM0_OSS
from a13n_service.memory.domain import CreateMemoryProviderRequest, MemoryEntries, MemoryEntrySelection
from a13n_service.memory.providers import MemoryProviderService
from a13n_service.memory.resources import MemoryProviderError, require_memory_configuration, require_provider
from a13n_service.storage import short_session
from pydantic import ValidationError
from tests.models.conftest import ORG_ID, WORKSPACE_ID, actor, protector


def document(**updates):
    return {
        "name": "project",
        "mode": "documents",
        "description": "Project facts",
        "backend": {"type": "filesystem"},
        **updates,
    }


def test_entries_roundtrip_and_mode_validation():
    records = {
        "name": "preferences",
        "mode": "records",
        "description": "Personal facts",
        "backend": {"provider_id": "memprov_1234567890abcdef"},
    }
    selection = MemoryEntries(entries=[document(), records])
    assert MemoryEntries.model_validate_json(selection.model_dump_json()) == selection
    assert "auto_recall" not in selection.model_dump()["entries"][0]
    assert "auto_organize" not in selection.model_dump()["entries"][1]
    with pytest.raises(ValidationError):
        MemoryEntries(entries=[document(), document()])
    with pytest.raises(ValidationError):
        MemoryEntries(entries=[])
    with pytest.raises(ValidationError):
        MemoryEntrySelection(**document(mode="records"))
    assert MemoryEntrySelection(**document(auto_organize=True)).auto_organize
    with pytest.raises(ValidationError):
        MemoryEntrySelection(**document(auto_recall=True))


@pytest.mark.anyio
async def test_filesystem_provider_needs_no_credential_and_rejects_records(memory_sessions):
    catalog = ProviderCatalog((FILESYSTEM, MEM0_OSS))
    providers = MemoryProviderService(memory_sessions, protector(), catalog)
    provider = await providers.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateMemoryProviderRequest(type="filesystem", name="Project files"),
    )
    assert provider.credential_configured is False
    definitions = await providers.provider_types(actor=actor())
    definition = next(item for item in definitions.items if item.type == "filesystem")
    assert definition.supports_documents and definition.supports_revisions
    assert definition.authentication.mode == "forbidden" and not definition.supports_records
    async with short_session(memory_sessions) as session:
        await require_provider(
            session,
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            provider_id=provider.id,
            eligible=True,
            catalog=catalog,
        )
        selection = MemoryEntries(entries=[document(backend={"provider_id": provider.id})])
        await require_memory_configuration(
            session, selection=selection, organization_id=ORG_ID, workspace_id=WORKSPACE_ID, catalog=catalog
        )
        with pytest.raises(MemoryProviderError, match="memory mode"):
            await require_memory_configuration(
                session,
                selection=MemoryEntries(entries=[document(mode="records", backend={"provider_id": provider.id})]),
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                catalog=catalog,
            )


@pytest.mark.anyio
async def test_inline_backends_are_validated_by_the_selected_definition(memory_sessions):
    catalog = ProviderCatalog((FILESYSTEM, MEM0_OSS))
    async with short_session(memory_sessions) as session:
        await require_memory_configuration(
            session,
            selection=MemoryEntries(entries=[document()]),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            catalog=catalog,
        )
        with pytest.raises(MemoryProviderError, match="configuration is invalid"):
            await require_memory_configuration(
                session,
                selection=MemoryEntries(
                    entries=[
                        document(backend={"type": "filesystem", "configuration": {"storage": {"root": "/a/../b"}}})
                    ]
                ),
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                catalog=catalog,
            )
        # A record backend is not a document backend, whatever its declared document support.
        with pytest.raises(MemoryProviderError, match="memory mode"):
            await require_memory_configuration(
                session,
                selection=MemoryEntries(entries=[document(backend={"type": "mem0_oss"})]),
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                catalog=catalog,
            )
