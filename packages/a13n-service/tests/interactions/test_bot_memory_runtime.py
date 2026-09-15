"""Real Attempt leases fence retained conversation memory and delegated access."""

import json
from uuid import uuid4

import httpx2
import pytest
from a13n_harness.capabilities.mem0_backends import Mem0OSSBackend
from a13n_service.application_errors import ApplicationError
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.interactions.inheritance import inherited_run_fields
from a13n_service.interactions.models import RunAttemptRecord, RunRecord
from a13n_service.memory.bots.binding import BotMemoryBinding
from a13n_service.memory.bots.domain import CreateDocument, MemorySettings, ScopeSettings
from a13n_service.memory.bots.models import ScopeRecord
from a13n_service.memory.bots.mutations import create
from a13n_service.memory.bots.runtime import bot_memory_capability
from a13n_service.memory.bots.selection import select_binding
from a13n_service.memory.bots.service import BotMemoryService
from a13n_service.memory.bots.verification import BotMemoryVerifier
from a13n_service.storage import transaction
from sqlalchemy import select

from tests.hooks.support import hook_actor
from tests.memory.support import memory_service

from .conftest import NOW, ORGANIZATION_ID, WORKSPACE_ID
from .worker_helpers import accepted_running_attempt

pytestmark = pytest.mark.anyio
ACCOUNT = "acct_botmemory1234567"
SCOPE = "mscope_botmemory1234567"


@pytest.fixture
def platform_state():
    return {"calls": [], "members": {}, "private": True, "after_check": None, "before_body": None}


@pytest.fixture
async def runtime_memory(interaction_sessions, interaction_object_store, monkeypatch, platform_state):
    run, context = await accepted_running_attempt(interaction_sessions, interaction_object_store)
    monkeypatch.setattr("a13n_service.memory.bots.access.utc_now", lambda: NOW)
    records, calls = {}, []

    async def handle(request):
        if request.url.host == "slack.com":
            platform_state["calls"].append(request.url.path)
            if request.url.path == "/api/auth.test":
                return httpx2.Response(
                    200, json={"ok": True, "team_id": "T1", "team": "Acme", "user_id": "U1", "bot_id": "B1"}
                )
            if request.url.path == "/api/bots.info":
                return httpx2.Response(
                    200,
                    json={
                        "ok": True,
                        "bot": {"id": "B1", "user_id": "U1", "app_id": "A1", "name": "Helper", "deleted": False},
                    },
                )
            assert request.url.path == "/api/conversations.info"
            channel_id = request.url.params["channel"]
            if platform_state["after_check"]:
                await platform_state["after_check"]()
            return httpx2.Response(
                200,
                json={
                    "ok": True,
                    "channel": {
                        "id": channel_id,
                        "name": channel_id,
                        "is_member": platform_state["members"].get(channel_id, True),
                        "is_archived": False,
                        "is_private": platform_state["private"],
                        "is_im": False,
                        "is_mpim": False,
                        "is_ext_shared": False,
                    },
                },
            )
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            body = json.loads(request.content)
            key = str(uuid4())
            records[key] = {
                "id": key,
                "memory": body["messages"][0]["content"],
                "run_id": body["run_id"],
                "metadata": body["metadata"],
            }
            return httpx2.Response(200, json={"results": [{"id": key, "event": "ADD"}]})
        if platform_state["before_body"]:
            await platform_state["before_body"]()
        key = request.url.path.split("/")[-1]
        return httpx2.Response(200, content=json.dumps(records.get(key)), headers={"content-type": "application/json"})

    async with httpx2.AsyncClient(base_url="http://oss/", transport=httpx2.MockTransport(handle)) as client:
        memory, provider, _ = await memory_service(
            interaction_sessions, Mem0OSSBackend(client), principal=hook_actor(), workspace_id=WORKSPACE_ID
        )
        async with transaction(interaction_sessions) as session:
            account = AccountRecord(
                id=ACCOUNT,
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                name="Bot",
                normalized_name="bot",
                provider_key="slack",
                provider_config_version="slack_http_v1",
                provider_config_json={"api_app_id": "A1", "team_id": "T1", "bot_user_id": "U1"},
                identity_digest="b" * 64,
                status="active",
                version=1,
                credential_generation=0,
                receive_enabled=False,
                default_agent_id=run.agent_id,
                created_by_type="user",
                created_by_id=hook_actor().principal.principal_id,
                memory_json=MemorySettings(provider_id=provider.id).model_dump(mode="json"),
                created_at=NOW,
                updated_at=NOW,
            )
            account.replace_credential(
                '{"bot_token":"synthetic","signing_secret":"synthetic-signing"}', memory.protector
            )
            session.add(account)
            await session.flush()
            session.add(
                ScopeRecord(
                    id=SCOPE,
                    organization_id=ORGANIZATION_ID,
                    workspace_id=WORKSPACE_ID,
                    account_id=ACCOUNT,
                    provider_id=provider.id,
                    external_conversation_id="C-engineering",
                    name="Engineering",
                    audience="private",
                    settings_json=ScopeSettings().model_dump(mode="json"),
                    version=1,
                    created_at=NOW,
                )
            )
            await session.flush()
            account = await session.get(AccountRecord, ACCOUNT)
            binding = await select_binding(session, account, "C-engineering")
            stored = await session.get(RunRecord, run.id)
            stored.bot_memory_json = binding.model_dump(mode="json")
            run = stored.to_resource()
        async with transaction(interaction_sessions) as session:
            role = await session.scalar(
                select(RoleBindingRecord).where(
                    RoleBindingRecord.workspace_id == WORKSPACE_ID,
                    RoleBindingRecord.principal_id == hook_actor().principal.principal_id,
                    RoleBindingRecord.resource_type == "workspace",
                )
            )
            original_role = role.role_key
            role.role_key = "admin"
        memory.bot_verifier = BotMemoryVerifier(interaction_sessions, client, EndpointPolicy(), memory.protector)
        service = BotMemoryService(memory)
        document = await create(
            service, hook_actor(), ACCOUNT, SCOPE, CreateDocument(text="Group evidence", title="Release"), "seed"
        )
        async with transaction(interaction_sessions) as session:
            role = await session.get(RoleBindingRecord, role.id)
            role.role_key = original_role
        yield memory, service, run, context, document, calls


async def test_retained_binding_survives_agent_and_provider_selection_changes(runtime_memory, interaction_sessions):
    memory, _service, run, context, document, calls = runtime_memory
    capability = bot_memory_capability(memory, run=run, agent_id=run.agent_id, current_context=lambda: context)
    assert capability is not None
    assert inherited_run_fields(run)["bot_memory"] == run.bot_memory
    async with transaction(interaction_sessions) as session:
        account = await session.get(AccountRecord, ACCOUNT)
        account.default_agent_id = None
        # A later Provider selection must not redirect an already accepted binding.
        account.memory_json = MemorySettings(provider_id="mp_otherprovider123456").model_dump(mode="json")
        account.version += 1
    before = len(calls)
    index = await capability.document_store.index()
    assert document.id in index.text and "Group evidence" not in index.text
    assert len(calls) == before
    assert (await capability.document_store.read(document.id)).text == "Group evidence"
    async with transaction(interaction_sessions) as session:
        account = await session.get(AccountRecord, ACCOUNT)
        account.memory_json = {**account.memory_json, "use_memory": False}
    before = len(calls)
    with pytest.raises(ApplicationError):
        await capability.document_store.read(document.id)
    assert len(calls) == before


async def test_scope_and_attempt_revocation_block_before_native_read(runtime_memory, interaction_sessions):
    memory, _service, run, context, document, calls = runtime_memory
    capability = bot_memory_capability(memory, run=run, agent_id=run.agent_id, current_context=lambda: context)
    async with transaction(interaction_sessions) as session:
        scope = await session.get(ScopeRecord, SCOPE)
        scope.audience = "unknown"
    before = len(calls)
    with pytest.raises(ApplicationError):
        await capability.document_store.read(document.id)
    assert len(calls) == before
    async with transaction(interaction_sessions) as session:
        scope = await session.get(ScopeRecord, SCOPE)
        scope.audience = "private"
        attempt = await session.get(RunAttemptRecord, context.run_attempt_id)
        attempt.lease_token_digest = "0" * 64
    from a13n_service.interactions.attempts import AttemptAuthorityError

    with pytest.raises(AttemptAuthorityError):
        await capability.document_store.read(document.id)
    assert len(calls) == before


async def test_forged_group_binding_and_unconfigured_group_cannot_expand_memory(runtime_memory, interaction_sessions):
    memory, _service, run, context, _document, calls = runtime_memory
    async with transaction(interaction_sessions) as session:
        original = await session.get(ScopeRecord, SCOPE)
        values = {column.key: getattr(original, column.key) for column in ScopeRecord.__table__.columns}
        values.update(id="mscope_othergroup123456", external_conversation_id="C-other")
        session.add(ScopeRecord(**values))
        account = await session.get(AccountRecord, ACCOUNT)
        disabled = await select_binding(session, account, "C-not-configured")
    assert disabled is not None and disabled.scope_id is None and not disabled.use_memory
    assert (
        bot_memory_capability(
            memory,
            run=run.model_copy(update={"bot_memory": disabled}),
            agent_id=run.agent_id,
            current_context=lambda: context,
        )
        is None
    )
    forged = BotMemoryBinding(
        account_id=ACCOUNT,
        external_conversation_id="C-other",
        provider_id=run.bot_memory.provider_id,
        scope_id="mscope_othergroup123456",
        scope_version=1,
        use_memory=True,
    )
    capability = bot_memory_capability(
        memory,
        run=run.model_copy(update={"bot_memory": forged}),
        agent_id=run.agent_id,
        current_context=lambda: context,
    )
    before = len(calls)
    with pytest.raises(ApplicationError):
        await capability.document_store.index()
    assert len(calls) == before


@pytest.mark.parametrize("membership", [False, None])
async def test_platform_removal_or_unknown_membership_blocks_all_memory_io(runtime_memory, platform_state, membership):
    memory, _service, run, context, document, calls = runtime_memory
    capability = bot_memory_capability(memory, run=run, agent_id=run.agent_id, current_context=lambda: context)
    store = capability.document_store
    platform_state["members"]["C-engineering"] = membership
    before = len(calls)
    with pytest.raises(ApplicationError) as error:
        await store.index()
    assert error.value.code == "memory_scope_unverified"
    with pytest.raises(ApplicationError):
        await store.read(document.id)
    with pytest.raises(ApplicationError):
        await store.create(
            "New evidence", title="Denied", description="", kind="long_term", correction_of=None, request_key="request1"
        )
    assert len(calls) == before


async def test_live_unknown_audience_does_not_trust_previous_private_flag(runtime_memory, platform_state):
    memory, _service, run, context, document, calls = runtime_memory
    capability = bot_memory_capability(memory, run=run, agent_id=run.agent_id, current_context=lambda: context)
    platform_state["private"] = None
    before = len(calls)
    with pytest.raises(ApplicationError):
        await capability.document_store.read(document.id)
    assert len(calls) == before


async def test_credential_rotation_after_platform_check_blocks_body_dispatch(
    runtime_memory, platform_state, interaction_sessions
):
    memory, _service, run, context, document, calls = runtime_memory

    async def rotate():
        async with transaction(interaction_sessions) as session:
            account = await session.get(AccountRecord, ACCOUNT)
            account.replace_credential(
                '{"bot_token":"replacement","signing_secret":"replacement-signing"}', memory.protector
            )

    platform_state["after_check"] = rotate
    capability = bot_memory_capability(memory, run=run, agent_id=run.agent_id, current_context=lambda: context)
    before = len(calls)
    with pytest.raises(ApplicationError) as error:
        await capability.document_store.read(document.id)
    assert error.value.code == "memory_scope_unverified"
    assert len(calls) == before


async def test_shared_source_group_is_verified_before_its_body_or_index_is_returned(
    runtime_memory, platform_state, interaction_sessions
):
    from a13n_service.memory.bots.domain import SharingPolicyInput
    from a13n_service.memory.bots.sharing import save_policy

    memory, service, run, context, _document, calls = runtime_memory
    other_id = "mscope_sharedsource12345"
    async with transaction(interaction_sessions) as session:
        original = await session.get(ScopeRecord, SCOPE)
        values = {column.key: getattr(original, column.key) for column in ScopeRecord.__table__.columns}
        values.update(id=other_id, external_conversation_id="C-source", name="Source")
        session.add(ScopeRecord(**values))
        role = await session.scalar(
            select(RoleBindingRecord).where(
                RoleBindingRecord.workspace_id == WORKSPACE_ID,
                RoleBindingRecord.principal_id == hook_actor().principal.principal_id,
                RoleBindingRecord.resource_type == "workspace",
            )
        )
        original_role = role.role_key
        role.role_key = "admin"
    source = await create(
        service,
        hook_actor(),
        ACCOUNT,
        other_id,
        CreateDocument(text="Shared source evidence", title="Shared release"),
        "shared-seed",
    )
    await save_policy(
        service,
        hook_actor(),
        ACCOUNT,
        SharingPolicyInput(name="Explicit collaboration", scope_ids=(SCOPE, other_id), include_history=True),
    )
    async with transaction(interaction_sessions) as session:
        row = await session.get(RoleBindingRecord, role.id)
        row.role_key = original_role
    capability = bot_memory_capability(memory, run=run, agent_id=run.agent_id, current_context=lambda: context)
    assert source.id in (await capability.document_store.index()).text
    assert (await capability.document_store.read(source.id)).text == "Shared source evidence"
    platform_state["members"]["C-source"] = False
    before = len(calls)
    with pytest.raises(ApplicationError):
        await capability.document_store.index()
    with pytest.raises(ApplicationError):
        await capability.document_store.read(source.id)
    assert len(calls) == before
    assert platform_state["calls"].count("/api/conversations.info") >= 8


async def test_credential_rotation_during_body_read_prevents_return(
    runtime_memory, platform_state, interaction_sessions
):
    memory, _service, run, context, document, calls = runtime_memory

    async def rotate():
        async with transaction(interaction_sessions) as session:
            account = await session.get(AccountRecord, ACCOUNT)
            account.replace_credential(
                '{"bot_token":"replacement","signing_secret":"replacement-signing"}', memory.protector
            )

    platform_state["before_body"] = rotate
    capability = bot_memory_capability(memory, run=run, agent_id=run.agent_id, current_context=lambda: context)
    before = len(calls)
    with pytest.raises(ApplicationError) as error:
        await capability.document_store.read(document.id)
    assert error.value.code == "memory_scope_unverified"
    assert len(calls) == before + 1
