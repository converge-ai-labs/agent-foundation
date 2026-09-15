import json
from dataclasses import replace

import httpx2
import pytest
from a13n_harness.capabilities.mem0_backends import Mem0OSSBackend
from a13n_harness.memory_plugins import Mem0OSSPlugin, MemoryBackendCatalog
from a13n_service.etags import resource_etag
from a13n_service.iam import AuthorizationError
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.memory.domain import CreateMemoryProviderRequest, MemoryScope, UpdateMemoryProviderRequest
from a13n_service.memory.models import MemoryProviderRecord
from a13n_service.memory.providers import MemoryProviderService
from a13n_service.memory.resources import MemoryProviderError
from a13n_service.storage import transaction
from pydantic import ValidationError
from tests.models.conftest import ORG_ID, WORKSPACE_ID, actor, protector

from .support import memory_service
from .test_api import native_transport

pytestmark = pytest.mark.anyio


def request(name="Memory"):
    return CreateMemoryProviderRequest(
        type="a13n.mem0-oss",
        name=name,
        configuration={"base_url": "http://unopened.invalid/"},
        credential={"api_key": "private-key"},
    )


def providers(sessions):
    return MemoryProviderService(sessions, protector(), MemoryBackendCatalog((Mem0OSSPlugin(),)))


async def test_provider_schema_encryption_etags_and_immutable_target(memory_sessions):
    service = providers(memory_sessions)
    definitions = await service.type_definitions(actor=actor())
    assert [item.type for item in definitions.items] == ["a13n.mem0-oss"]
    assert definitions.items[0].credential_schema["writeOnly"] is True
    first = await service.create(actor=actor(), workspace_id=WORKSPACE_ID, request=request())
    assert first.configuration == {"base_url": "http://unopened.invalid"}
    assert first.credential_configured
    assert "private-key" not in first.model_dump_json()
    async with transaction(memory_sessions) as session:
        record = await session.get(MemoryProviderRecord, first.id)
        assert b"private-key" not in record.ciphertext
        assert json.loads(record.credential_snapshot().decrypt(protector())) == {"api_key": "private-key"}
        assert record.credential_generation == 1
    etag = resource_etag(first.id, first.updated_at)
    updated = await service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=first.id,
        if_match=etag,
        request=UpdateMemoryProviderRequest(name="Renamed", credential={"api_key": "new-key"}),
    )
    assert updated.id == first.id and updated.configuration == first.configuration
    assert updated.updated_at > first.updated_at
    with pytest.raises(MemoryProviderError) as stale:
        await service.update(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            provider_id=first.id,
            if_match=etag,
            request=UpdateMemoryProviderRequest(enabled=False),
        )
    assert stale.value.code == "precondition_failed"
    for change in ({"type": "other.backend"}, {"configuration": {}}, {"name": None}, {}):
        with pytest.raises(ValidationError):
            UpdateMemoryProviderRequest.model_validate(change)
    with pytest.raises(MemoryProviderError) as duplicate:
        await service.create(actor=actor(), workspace_id=WORKSPACE_ID, request=request("RENAMED"))
    assert duplicate.value.code == "memory_provider_name_conflict"
    # Two resources may intentionally use one endpoint; namespaces distinguish them.
    second = await service.create(actor=actor(), workspace_id=WORKSPACE_ID, request=request("Second"))
    assert second.id != first.id and second.configuration == first.configuration
    page = await service.list(actor=actor(), workspace_id=WORKSPACE_ID, limit=1)
    assert len(page.items) == 1 and page.next_cursor
    tail = await service.list(actor=actor(), workspace_id=WORKSPACE_ID, limit=1, cursor=page.next_cursor)
    assert len(tail.items) == 1 and tail.next_cursor is None
    with pytest.raises(MemoryProviderError) as mismatch:
        await service.list(actor=actor(), workspace_id=WORKSPACE_ID, limit=1, cursor=page.next_cursor, enabled=False)
    assert mismatch.value.code == "invalid_cursor"


async def test_organization_visibility_does_not_allow_workspace_mutation(memory_sessions):
    async with transaction(memory_sessions) as session:
        binding = await session.get(RoleBindingRecord, "rb_org1234567890abcd")
        binding.role_key = "admin"
    org_actor = replace(actor(), boundary_workspace_id=None, boundary_organization_id=ORG_ID)
    service = providers(memory_sessions)
    provider = await service.create(actor=org_actor, workspace_id=None, request=request())
    visible = await service.get(actor=actor(), workspace_id=WORKSPACE_ID, provider_id=provider.id)
    assert visible.workspace_id is None and visible.organization_id == ORG_ID
    with pytest.raises(MemoryProviderError) as wrong_owner:
        await service.update(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            provider_id=provider.id,
            if_match=resource_etag(provider.id, provider.updated_at),
            request=UpdateMemoryProviderRequest(enabled=False),
        )
    assert wrong_owner.value.code == "memory_provider_not_found"
    with pytest.raises(AuthorizationError):
        await service.get(actor=actor(), workspace_id="ws_other1234567890", provider_id=provider.id)


async def test_live_credentials_disable_and_cleanup_preserve_records(memory_sessions):
    records, calls = {}, []
    async with httpx2.AsyncClient(
        base_url="http://oss/", transport=httpx2.MockTransport(native_transport(records, calls, httpx2.Response))
    ) as client:
        service, provider, plugin = await memory_service(memory_sessions, Mem0OSSBackend(client))
        kwargs = dict(
            actor=actor(), workspace_id=WORKSPACE_ID, provider_id=provider.id, selection=MemoryScope(scope="user")
        )
        memory = await service.add(**kwargs, text="Keep this record")
        management = MemoryProviderService(memory_sessions, protector(), service.catalog)
        rotated = await management.update(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            provider_id=provider.id,
            if_match=resource_etag(provider.id, provider.updated_at),
            request=UpdateMemoryProviderRequest(credential={"api_key": "rotated-key"}),
        )
        assert (await service.get(**kwargs, memory_id=memory.id)).memory == "Keep this record"
        assert plugin.credentials == ["first-key", "rotated-key"]
        await management.update(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            provider_id=provider.id,
            if_match=resource_etag(rotated.id, rotated.updated_at),
            request=UpdateMemoryProviderRequest(enabled=False),
        )
        count = len(calls)
        with pytest.raises(MemoryProviderError) as disabled:
            await service.get(**kwargs, memory_id=memory.id)
        assert disabled.value.code == "memory_provider_disabled"
        assert len(calls) == count and memory.id in records
        assert plugin.opened == plugin.closed == 2


async def test_viewer_reads_resource_but_cannot_rotate_credential(memory_sessions):
    service = providers(memory_sessions)
    provider = await service.create(actor=actor(), workspace_id=WORKSPACE_ID, request=request())
    async with transaction(memory_sessions) as session:
        binding = await session.get(RoleBindingRecord, "rb_ws1234567890abcde")
        binding.role_key = "viewer"
    assert (await service.get(actor=actor(), workspace_id=WORKSPACE_ID, provider_id=provider.id)).id == provider.id
    with pytest.raises(AuthorizationError):
        await service.update(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            provider_id=provider.id,
            if_match=resource_etag(provider.id, provider.updated_at),
            request=UpdateMemoryProviderRequest(credential={"api_key": "forbidden"}),
        )
