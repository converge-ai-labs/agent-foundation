import a13n_service.web.probe as probe_module
import httpx2
import pytest
from a13n_harness.capabilities.web import (
    WebProviderError as HarnessWebProviderError,
)
from a13n_harness.capabilities.web import (
    WebScrapeResult,
    WebSearchResponse,
)
from a13n_service.etags import resource_etag
from a13n_service.iam import AuthorizationError
from a13n_service.iam.models import RoleBindingRecord, SecurityAuditRecord, WorkspaceRecord
from a13n_service.provider_plugins import WebProviderRegistration
from a13n_service.storage import transaction
from a13n_service.temporal import utc_now
from a13n_service.web.cleanup import WebProviderOwnerCleanup
from a13n_service.web.domain import CreateWebProviderRequest, UpdateWebProviderRequest
from a13n_service.web.models import WebProviderRecord
from a13n_service.web.probe import test_account as probe_account
from a13n_service.web.registry import WebProviderRegistry
from a13n_service.web.resources import WebProviderError
from a13n_service.web.service import WebProviderService
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import select

from ..models.conftest import WORKSPACE_ID, actor, protector
from ..resource_scope_helpers import organization_admin, sibling_workspace
from .test_adapters import transport

pytestmark = pytest.mark.anyio


class _EmptyConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _NestedCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key: SecretStr
    nested: dict[str, SecretStr]
    tenant: str | None = "default"


class _ProbeRuntime:
    def __init__(self, *, failure: str | None = None) -> None:
        self.calls: list[str] = []
        self.closed = 0
        self.failure = failure
        self.credentials: list[_NestedCredentials] = []

    async def search(self, *, credentials, **_kwargs):
        self.calls.append("search")
        self.credentials.append(_NestedCredentials.model_validate(credentials))
        if self.failure is not None:
            raise HarnessWebProviderError(self.failure)
        return WebSearchResponse(results=())

    async def scrape(self, *, credentials, request, policy, **_kwargs):
        self.calls.append("scrape")
        self.credentials.append(_NestedCredentials.model_validate(credentials))
        await policy.authorize(request.url, purpose="scrape")
        if self.failure is not None:
            raise HarnessWebProviderError(self.failure)
        return WebScrapeResult(content="", source_url=request.url, canonical_url=request.url)

    async def aclose(self) -> None:
        self.closed += 1


def _custom_service(web_sessions, secret_protector, runtime, *, search: bool, scrape: bool) -> WebProviderService:
    registration = WebProviderRegistration(
        type="custom_web",
        display_name="Custom Web",
        configuration_model=_EmptyConfiguration,
        credential_model=_NestedCredentials,
        setup_url="https://example.com/setup",
        factory=lambda: runtime,
        supports_search=search,
        supports_scrape=scrape,
    )
    return WebProviderService(web_sessions, secret_protector, WebProviderRegistry((registration,)))


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


async def test_nested_secret_credentials_survive_create_rotation_and_runtime(web_sessions) -> None:
    secret_protector = protector()
    runtime = _ProbeRuntime()
    service = _custom_service(web_sessions, secret_protector, runtime, search=True, scrape=False)
    account = await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateWebProviderRequest(
            type="custom_web",
            name="Custom",
            credential={"api_key": "initial-secret", "nested": {"token": "nested-secret"}, "tenant": None},
        ),
    )
    assert "secret" not in account.model_dump_json()
    async with transaction(web_sessions) as session:
        record = await session.get(WebProviderRecord, account.id)
        saved = record.credential_snapshot().decrypt(secret_protector)
        assert "initial-secret" in saved and "nested-secret" in saved and "**********" not in saved

    rotated = await service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=account.id,
        if_match=resource_etag(account.id, account.updated_at),
        request=UpdateWebProviderRequest(
            credential={"api_key": "rotated-secret", "nested": {"token": "rotated-nested"}, "tenant": None}
        ),
    )
    result = await probe_account(
        service,
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=rotated.id,
    )
    assert result.success and runtime.calls == ["search"] and runtime.closed == 1
    assert runtime.credentials[0].api_key.get_secret_value() == "rotated-secret"
    assert runtime.credentials[0].nested["token"].get_secret_value() == "rotated-nested"
    assert runtime.credentials[0].tenant is None

    with pytest.raises(WebProviderError) as invalid:
        await service.update(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            provider_id=account.id,
            if_match=resource_etag(rotated.id, rotated.updated_at),
            request=UpdateWebProviderRequest(credential={"api_key": "must-not-leak", "nested": {"token": None}}),
        )
    assert "must-not-leak" not in str(invalid.value)


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


@pytest.mark.parametrize(
    ("search", "scrape", "expected"),
    [(True, False, "search"), (False, True, "scrape"), (True, True, "search")],
)
async def test_saved_probe_chooses_one_supported_operation_and_reauthorizes(
    web_sessions,
    monkeypatch,
    search,
    scrape,
    expected,
) -> None:
    runtime = _ProbeRuntime()
    service = _custom_service(web_sessions, protector(), runtime, search=search, scrape=scrape)
    account = await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateWebProviderRequest(
            type="custom_web",
            name="Probe",
            credential={"api_key": "secret", "nested": {"token": "nested"}},
        ),
    )
    authorize_scope = probe_module.authorize_scope
    authorizations = 0

    async def tracked_authorize_scope(*args, **kwargs):
        nonlocal authorizations
        authorizations += 1
        return await authorize_scope(*args, **kwargs)

    monkeypatch.setattr(probe_module, "authorize_scope", tracked_authorize_scope)
    result = await probe_account(
        service,
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=account.id,
    )
    assert result.success
    assert runtime.calls == [expected]
    assert runtime.closed == 1
    assert authorizations == 3  # Initial snapshot, post-dispatch reauthorization, and audit lock.


async def test_saved_probe_reports_failure_and_closes_runtime(web_sessions) -> None:
    runtime = _ProbeRuntime(failure="web_scrape_unavailable")
    service = _custom_service(web_sessions, protector(), runtime, search=False, scrape=True)
    account = await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateWebProviderRequest(
            type="custom_web",
            name="Failing",
            credential={"api_key": "secret", "nested": {"token": "nested"}},
        ),
    )
    result = await probe_account(
        service,
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=account.id,
    )
    assert not result.success and result.code == "web_scrape_unavailable"
    assert runtime.calls == ["scrape"] and runtime.closed == 1


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
