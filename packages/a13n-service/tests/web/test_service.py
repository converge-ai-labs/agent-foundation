import httpx2
import pytest
from a13n_service.etags import resource_etag
from a13n_service.iam import AuthorizationError
from a13n_service.iam.models import RoleBindingRecord, SecurityAuditRecord, WorkspaceRecord
from a13n_service.storage import transaction
from a13n_service.temporal import utc_now
from a13n_service.web.cleanup import WebProviderOwnerCleanup
from a13n_service.web.domain import CreateWebProviderRequest, UpdateWebProviderRequest
from a13n_service.web.models import WebProviderRecord
from a13n_service.web.probe import test_account as probe_account
from a13n_service.web.resources import WebProviderError
from sqlalchemy import select

from ..models.conftest import WORKSPACE_ID, actor, protector
from ..resource_scope_helpers import organization_admin, sibling_workspace
from .test_adapters import transport

pytestmark = pytest.mark.anyio


async def create(service, name="Search", **kwargs):
    return await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateWebProviderRequest(type="exa", name=name, credential={"api_key": "secret-key"}, **kwargs),
    )


async def test_secrets_etags_rotation_and_noop(web_service, web_sessions) -> None:
    account = await create(web_service)
    assert account.id.startswith("wprov_") and account.credential_configured
    assert "secret-key" not in account.model_dump_json()
    etag = resource_etag(account.id, account.updated_at)
    unchanged = await web_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=account.id,
        if_match=etag,
        request=UpdateWebProviderRequest(name="Search"),
    )
    assert unchanged == account
    async with transaction(web_sessions) as session:
        record = await session.get(WebProviderRecord, account.id)
        assert record.credential_snapshot().decrypt(protector()) == '{"api_key": "secret-key"}'
        initial_generation, initial_ciphertext = record.credential_generation, record.ciphertext
    rotated = await web_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=account.id,
        if_match=etag,
        request=UpdateWebProviderRequest(credential={"api_key": "secret-key"}),
    )
    assert resource_etag(rotated.id, rotated.updated_at) != etag
    async with transaction(web_sessions) as session:
        record = await session.get(WebProviderRecord, account.id)
        assert record.credential_generation == initial_generation + 1
        assert record.ciphertext != initial_ciphertext
        audits = (
            await session.scalars(select(SecurityAuditRecord).where(SecurityAuditRecord.resource_id == account.id))
        ).all()
        assert len(audits) == 2
        assert "secret-key" not in repr([row.details for row in audits])
    with pytest.raises(WebProviderError) as stale:
        await web_service.update(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            provider_id=account.id,
            if_match=etag,
            request=UpdateWebProviderRequest(enabled=False),
        )
    assert stale.value.code == "precondition_failed"
    with pytest.raises(WebProviderError) as duplicate:
        await create(web_service, "SEARCH")
    assert duplicate.value.code == "web_provider_name_conflict"


async def test_scope_visibility_owning_mutations_and_pagination(web_service, web_sessions) -> None:
    admin = await organization_admin(web_sessions, actor())
    org = await web_service.create(
        actor=admin,
        workspace_id=None,
        request=CreateWebProviderRequest(type="brave", name="Same", credential={"api_key": "key"}),
    )
    local = await create(web_service, "same")
    sibling_id = await sibling_workspace(web_sessions, admin)
    sibling = await web_service.create(
        actor=admin,
        workspace_id=sibling_id,
        request=CreateWebProviderRequest(type="exa", name="Sibling", credential={"api_key": "key"}),
    )
    visible = await web_service.list(actor=actor(), workspace_id=WORKSPACE_ID, limit=1)
    second = await web_service.list(actor=actor(), workspace_id=WORKSPACE_ID, cursor=visible.next_cursor)
    assert {x.id for x in (*visible.items, *second.items)} == {org.id, local.id}
    assert second.next_cursor is None
    assert (await web_service.list(actor=admin, workspace_id=None)).items == (org,)
    for target in (org, sibling):
        with pytest.raises(WebProviderError) as hidden:
            await web_service.update(
                actor=actor(),
                workspace_id=WORKSPACE_ID,
                provider_id=target.id,
                if_match=resource_etag(target.id, target.updated_at),
                request=UpdateWebProviderRequest(enabled=False),
            )
        assert hidden.value.code == "web_provider_not_found"
    with pytest.raises(WebProviderError, match="cursor"):
        await web_service.list(actor=actor(), workspace_id=WORKSPACE_ID, cursor=visible.next_cursor, enabled=False)


async def test_viewer_read_does_not_grant_management(web_service, web_sessions) -> None:
    account = await create(web_service)
    async with transaction(web_sessions) as session:
        role = await session.get(RoleBindingRecord, "rb_ws1234567890abcde")
        role.role_key = "viewer"
    assert (await web_service.get(actor=actor(), workspace_id=WORKSPACE_ID, provider_id=account.id)).id == account.id
    with pytest.raises(AuthorizationError):
        await create(web_service, "No")


@pytest.mark.parametrize("change", [{"credential": {"api_key": "new-key"}}, {"enabled": False}])
async def test_saved_probe_one_dispatch_and_concurrent_changes(web_service, web_sessions, change) -> None:
    account = await create(web_service)
    requests = []

    def rate_limited(request):
        requests.append(request)
        assert request.headers["x-api-key"] == "secret-key"
        return httpx2.Response(429, headers={"Retry-After": "0"})

    result = await probe_account(
        web_service,
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=account.id,
        transport=transport(rate_limited),
    )
    assert not result.success and result.code == "web_search_rate_limited"
    assert len(requests) == 1

    async def changed(_):
        await web_service.update(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            provider_id=account.id,
            if_match=resource_etag(account.id, account.updated_at),
            request=UpdateWebProviderRequest(**change),
        )
        return httpx2.Response(200, json={"results": []})

    with pytest.raises(WebProviderError) as conflict:
        await probe_account(
            web_service,
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            provider_id=account.id,
            transport=transport(changed),
        )
    assert conflict.value.code == "web_provider_changed"


async def test_workspace_cleanup_erases_material_but_retains_identity(web_service, web_sessions) -> None:
    account = await create(web_service)
    async with transaction(web_sessions) as session:
        workspace = await session.get(WorkspaceRecord, WORKSPACE_ID)
        workspace.deleted_at = utc_now()
    cleanup = WebProviderOwnerCleanup(web_sessions, batch_limit=10)
    assert (await cleanup.scan()).completed == 1
    assert (await cleanup.scan()).completed == 0
    async with transaction(web_sessions) as session:
        record = await session.get(WebProviderRecord, account.id)
        assert not record.enabled and record.ciphertext is None and record.nonce is None
