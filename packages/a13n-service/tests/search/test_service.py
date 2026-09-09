import httpx2
import pytest
from a13n_service.etags import resource_etag
from a13n_service.iam import AuthorizationError
from a13n_service.iam.models import RoleBindingRecord, SecurityAuditRecord, WorkspaceRecord
from a13n_service.search.cleanup import SearchProviderOwnerCleanup
from a13n_service.search.domain import CreateSearchProviderRequest, UpdateSearchProviderRequest
from a13n_service.search.models import SearchProviderRecord
from a13n_service.search.probe import test_account as probe_account
from a13n_service.search.resources import SearchProviderError
from a13n_service.storage import transaction
from a13n_service.temporal import utc_now
from sqlalchemy import select

from ..models.conftest import WORKSPACE_ID, actor, protector
from ..resource_scope_helpers import organization_admin, sibling_workspace
from .test_adapters import transport

pytestmark = pytest.mark.anyio


async def create(service, name="Search", **kwargs):
    return await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateSearchProviderRequest(type="exa", name=name, credential="secret-key", **kwargs),
    )


async def test_secrets_etags_rotation_and_noop(search_service, search_sessions) -> None:
    account = await create(search_service)
    assert account.id.startswith("sprov_") and account.credential_configured
    assert "secret-key" not in account.model_dump_json()
    etag = resource_etag(account.id, account.updated_at)
    unchanged = await search_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=account.id,
        if_match=etag,
        request=UpdateSearchProviderRequest(name="Search"),
    )
    assert unchanged == account
    async with transaction(search_sessions) as session:
        record = await session.get(SearchProviderRecord, account.id)
        assert record.credential_snapshot().decrypt(protector()) == "secret-key"
        initial_generation, initial_ciphertext = record.credential_generation, record.ciphertext
    rotated = await search_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=account.id,
        if_match=etag,
        request=UpdateSearchProviderRequest(credential="secret-key"),
    )
    assert resource_etag(rotated.id, rotated.updated_at) != etag
    async with transaction(search_sessions) as session:
        record = await session.get(SearchProviderRecord, account.id)
        assert record.credential_generation == initial_generation + 1
        assert record.ciphertext != initial_ciphertext
        audits = (
            await session.scalars(select(SecurityAuditRecord).where(SecurityAuditRecord.resource_id == account.id))
        ).all()
        assert len(audits) == 2
        assert "secret-key" not in repr([row.details for row in audits])
    with pytest.raises(SearchProviderError) as stale:
        await search_service.update(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            provider_id=account.id,
            if_match=etag,
            request=UpdateSearchProviderRequest(enabled=False),
        )
    assert stale.value.code == "precondition_failed"
    with pytest.raises(SearchProviderError) as duplicate:
        await create(search_service, "SEARCH")
    assert duplicate.value.code == "search_provider_name_conflict"


async def test_scope_visibility_owning_mutations_and_pagination(search_service, search_sessions) -> None:
    admin = await organization_admin(search_sessions, actor())
    org = await search_service.create(
        actor=admin, workspace_id=None, request=CreateSearchProviderRequest(type="brave", name="Same", credential="key")
    )
    local = await create(search_service, "same")
    sibling_id = await sibling_workspace(search_sessions, admin)
    sibling = await search_service.create(
        actor=admin,
        workspace_id=sibling_id,
        request=CreateSearchProviderRequest(type="exa", name="Sibling", credential="key"),
    )
    visible = await search_service.list(actor=actor(), workspace_id=WORKSPACE_ID, limit=1)
    second = await search_service.list(actor=actor(), workspace_id=WORKSPACE_ID, cursor=visible.next_cursor)
    assert {x.id for x in (*visible.items, *second.items)} == {org.id, local.id}
    assert second.next_cursor is None
    assert (await search_service.list(actor=admin, workspace_id=None)).items == (org,)
    for target in (org, sibling):
        with pytest.raises(SearchProviderError) as hidden:
            await search_service.update(
                actor=actor(),
                workspace_id=WORKSPACE_ID,
                provider_id=target.id,
                if_match=resource_etag(target.id, target.updated_at),
                request=UpdateSearchProviderRequest(enabled=False),
            )
        assert hidden.value.code == "search_provider_not_found"
    with pytest.raises(SearchProviderError, match="cursor"):
        await search_service.list(actor=actor(), workspace_id=WORKSPACE_ID, cursor=visible.next_cursor, enabled=False)


async def test_viewer_read_does_not_grant_management(search_service, search_sessions) -> None:
    account = await create(search_service)
    async with transaction(search_sessions) as session:
        role = await session.get(RoleBindingRecord, "rb_ws1234567890abcde")
        role.role_key = "viewer"
    assert (await search_service.get(actor=actor(), workspace_id=WORKSPACE_ID, provider_id=account.id)).id == account.id
    with pytest.raises(AuthorizationError):
        await create(search_service, "No")


@pytest.mark.parametrize("change", [{"credential": "new-key"}, {"enabled": False}])
async def test_saved_probe_one_dispatch_and_concurrent_changes(search_service, search_sessions, change) -> None:
    account = await create(search_service)
    requests = []

    def rate_limited(request):
        requests.append(request)
        assert request.headers["x-api-key"] == "secret-key"
        return httpx2.Response(429, headers={"Retry-After": "0"})

    result = await probe_account(
        search_service,
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=account.id,
        transport=transport(rate_limited),
    )
    assert not result.success and result.code == "web_search_rate_limited"
    assert len(requests) == 1

    async def changed(_):
        await search_service.update(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            provider_id=account.id,
            if_match=resource_etag(account.id, account.updated_at),
            request=UpdateSearchProviderRequest(**change),
        )
        return httpx2.Response(200, json={"results": []})

    with pytest.raises(SearchProviderError) as conflict:
        await probe_account(
            search_service,
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            provider_id=account.id,
            transport=transport(changed),
        )
    assert conflict.value.code == "search_provider_changed"


async def test_workspace_cleanup_erases_material_but_retains_identity(search_service, search_sessions) -> None:
    account = await create(search_service)
    async with transaction(search_sessions) as session:
        workspace = await session.get(WorkspaceRecord, WORKSPACE_ID)
        workspace.deleted_at = utc_now()
    cleanup = SearchProviderOwnerCleanup(search_sessions, batch_limit=10)
    assert (await cleanup.scan()).completed == 1
    assert (await cleanup.scan()).completed == 0
    async with transaction(search_sessions) as session:
        record = await session.get(SearchProviderRecord, account.id)
        assert not record.enabled and record.ciphertext is None and record.nonce is None
