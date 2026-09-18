"""Real Attempt leases fence retained conversation memory and delegated access."""

import json
from uuid import uuid4

import httpx2
import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.memory.mem0_oss import Mem0OSSBackend
from a13n_service.application_errors import ApplicationError
from a13n_service.bots.memory.binding import BotMemoryBinding
from a13n_service.bots.memory.bindings import bind, require_binding
from a13n_service.bots.memory.domain import CreateDocument, ScopeSettings
from a13n_service.bots.memory.models import ScopeRecord
from a13n_service.bots.memory.mutations import create
from a13n_service.bots.memory.runtime import bot_memory_capability
from a13n_service.bots.memory.selection import select_binding
from a13n_service.bots.memory.service import BotMemoryService
from a13n_service.bots.memory.settings import AccountSettingsRecord
from a13n_service.bots.memory.verification import BotMemoryVerifier
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.interactions.inheritance import inherited_run_fields
from a13n_service.interactions.models import RunAttemptRecord, RunRecord
from a13n_service.memory.models import MemoryProviderRecord
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
    monkeypatch.setattr("a13n_service.bots.memory.access.utc_now", lambda: NOW)
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
                created_at=NOW,
                updated_at=NOW,
            )
            account.replace_credential(
                '{"bot_token":"synthetic","signing_secret":"synthetic-signing"}', memory.protector
            )
            session.add(account)
            await session.flush()
            session.add(
                AccountSettingsRecord(
                    account_id=ACCOUNT,
                    version=1,
                    provider_id=provider.id,
                    use_memory=True,
                    save_on_request=True,
                    timezone="UTC",
                )
            )
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
            from a13n_service.memory.behaviors import RunMemorySelectionRecord

            await session.delete(await session.get(RunMemorySelectionRecord, run.id))
            await session.flush()
            await bind(session, run.id, binding)
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
        verifier = BotMemoryVerifier(interaction_sessions, client, EndpointPolicy(), memory.protector)
        service = BotMemoryService(memory)
        document = await create(
            service, hook_actor(), ACCOUNT, SCOPE, CreateDocument(text="Group evidence", title="Release"), "seed"
        )
        async with transaction(interaction_sessions) as session:
            role = await session.get(RoleBindingRecord, role.id)
            role.role_key = original_role
        yield memory, service, run, context, document, calls, binding, verifier


async def test_retained_binding_survives_agent_and_provider_selection_changes(runtime_memory, interaction_sessions):
    memory, _service, run, context, document, calls, binding, verifier = runtime_memory
    capability = bot_memory_capability(
        memory, run=run, binding=binding, verifier=verifier, agent_id=run.agent_id, current_context=lambda: context
    )
    assert capability is not None
    assert "bot_memory" not in inherited_run_fields(run)
    async with transaction(interaction_sessions) as session:
        assert await require_binding(session, run.id) == binding
    async with transaction(interaction_sessions) as session:
        account = await session.get(AccountRecord, ACCOUNT)
        account.default_agent_id = None
        # A later Provider selection must not redirect an already accepted binding.
        original = await session.get(MemoryProviderRecord, binding.provider_id)
        values = {column.key: getattr(original, column.key) for column in MemoryProviderRecord.__table__.columns}
        values.update(id="mp_otherprovider123456", name="Other memory", normalized_name="other memory")
        session.add(MemoryProviderRecord(**values))
        await session.flush()
        settings = await session.get(AccountSettingsRecord, ACCOUNT)
        settings.provider_id = "mp_otherprovider123456"
        settings.version += 1
        account.version += 1
    before = len(calls)
    index = await capability.document_store.index()
    assert document.id in index.text and "Group evidence" not in index.text
    assert len(calls) == before
    assert (await capability.document_store.read(document.id)).text == "Group evidence"
    async with transaction(interaction_sessions) as session:
        account = await session.get(AccountRecord, ACCOUNT)
        settings = await session.get(AccountSettingsRecord, ACCOUNT)
        settings.use_memory = False
        settings.version += 1
    before = len(calls)
    with pytest.raises(ApplicationError):
        await capability.document_store.read(document.id)
    assert len(calls) == before


async def test_scope_and_attempt_revocation_block_before_native_read(runtime_memory, interaction_sessions):
    memory, _service, run, context, document, calls, binding, verifier = runtime_memory
    capability = bot_memory_capability(
        memory, run=run, binding=binding, verifier=verifier, agent_id=run.agent_id, current_context=lambda: context
    )
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
    memory, _service, run, context, _document, calls, binding, verifier = runtime_memory
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
            run=run,
            binding=disabled,
            verifier=verifier,
            agent_id=run.agent_id,
            current_context=lambda: context,
        )
        is None
    )
    forged = BotMemoryBinding(
        account_id=ACCOUNT,
        external_conversation_id="C-other",
        provider_id=binding.provider_id,
        scope_id="mscope_othergroup123456",
        scope_version=1,
        use_memory=True,
    )
    capability = bot_memory_capability(
        memory,
        run=run,
        binding=forged,
        verifier=verifier,
        agent_id=run.agent_id,
        current_context=lambda: context,
    )
    before = len(calls)
    with pytest.raises(ApplicationError):
        await capability.document_store.index()
    assert len(calls) == before


@pytest.mark.parametrize("membership", [False, None])
async def test_platform_removal_or_unknown_membership_blocks_all_memory_io(runtime_memory, platform_state, membership):
    memory, _service, run, context, document, calls, binding, verifier = runtime_memory
    capability = bot_memory_capability(
        memory, run=run, binding=binding, verifier=verifier, agent_id=run.agent_id, current_context=lambda: context
    )
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
    memory, _service, run, context, document, calls, binding, verifier = runtime_memory
    capability = bot_memory_capability(
        memory, run=run, binding=binding, verifier=verifier, agent_id=run.agent_id, current_context=lambda: context
    )
    platform_state["private"] = None
    before = len(calls)
    with pytest.raises(ApplicationError):
        await capability.document_store.read(document.id)
    assert len(calls) == before


async def test_credential_rotation_after_platform_check_blocks_body_dispatch(
    runtime_memory, platform_state, interaction_sessions
):
    memory, _service, run, context, document, calls, binding, verifier = runtime_memory

    async def rotate():
        async with transaction(interaction_sessions) as session:
            account = await session.get(AccountRecord, ACCOUNT)
            account.replace_credential(
                '{"bot_token":"replacement","signing_secret":"replacement-signing"}', memory.protector
            )

    platform_state["after_check"] = rotate
    capability = bot_memory_capability(
        memory, run=run, binding=binding, verifier=verifier, agent_id=run.agent_id, current_context=lambda: context
    )
    before = len(calls)
    with pytest.raises(ApplicationError) as error:
        await capability.document_store.read(document.id)
    assert error.value.code == "memory_scope_unverified"
    assert len(calls) == before


async def test_shared_source_group_is_verified_before_its_body_or_index_is_returned(
    runtime_memory, platform_state, interaction_sessions
):

    memory, service, run, context, _document, calls, binding, verifier = runtime_memory
    other_id = "mscope_sharedsource12345"
    async with transaction(interaction_sessions) as session:
        original = await session.get(ScopeRecord, SCOPE)
        values = {column.key: getattr(original, column.key) for column in ScopeRecord.__table__.columns}
        values.update(
            id=other_id,
            external_conversation_id="C-source",
            name="Source",
            settings_json=ScopeSettings(visibility="installation").model_dump(),
        )
        session.add(ScopeRecord(**values))
        from a13n_service.connectivity.accounts.target_models import AccountTargetRecord

        for external_id in ("C-engineering", "C-source"):
            session.add(
                AccountTargetRecord(
                    id=f"atgt_{external_id}",
                    account_id=ACCOUNT,
                    organization_id=ORGANIZATION_ID,
                    workspace_id=WORKSPACE_ID,
                    target_kind="conversation",
                    external_target_id=external_id,
                    version=1,
                    receive_enabled=True,
                    created_by_type="user",
                    created_by_id=hook_actor().principal.principal_id,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )

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
    async with transaction(interaction_sessions) as session:
        row = await session.get(RoleBindingRecord, role.id)
        row.role_key = original_role
    capability = bot_memory_capability(
        memory, run=run, binding=binding, verifier=verifier, agent_id=run.agent_id, current_context=lambda: context
    )
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
    platform_state["members"]["C-source"] = True

    async def revoke_during_read():
        async with transaction(interaction_sessions) as session:
            source_scope = await session.get(ScopeRecord, other_id)
            source_scope.settings_json = {**source_scope.settings_json, "visibility": "group"}
            source_scope.version += 1

    platform_state["before_body"] = revoke_during_read
    with pytest.raises(ApplicationError) as revoked:
        await capability.document_store.read(source.id)
    assert revoked.value.code == "memory_not_found"
    platform_state["before_body"] = None
    assert source.id not in (await capability.document_store.index()).text


async def test_credential_rotation_during_body_read_prevents_return(
    runtime_memory, platform_state, interaction_sessions
):
    memory, _service, run, context, document, calls, binding, verifier = runtime_memory

    async def rotate():
        async with transaction(interaction_sessions) as session:
            account = await session.get(AccountRecord, ACCOUNT)
            account.replace_credential(
                '{"bot_token":"replacement","signing_secret":"replacement-signing"}', memory.protector
            )

    platform_state["before_body"] = rotate
    capability = bot_memory_capability(
        memory, run=run, binding=binding, verifier=verifier, agent_id=run.agent_id, current_context=lambda: context
    )
    before = len(calls)
    with pytest.raises(ApplicationError) as error:
        await capability.document_store.read(document.id)
    assert error.value.code == "memory_scope_unverified"
    assert len(calls) == before + 1


async def test_missing_or_unknown_selection_never_falls_back(
    runtime_memory, interaction_sessions, interaction_object_store
):
    from a13n_harness.errors import RunError
    from a13n_service.bots.memory.behavior import ConversationMemory
    from a13n_service.interactions.objects import RunStateStore
    from a13n_service.memory.behaviors import MemoryBehaviors, RunMemorySelectionRecord
    from a13n_service.memory.ordinary import OrdinaryMemory

    memory, _, run, context, _, _, _binding, verifier = runtime_memory
    registry = MemoryBehaviors(
        interaction_sessions, default=OrdinaryMemory(memory), behaviors=(ConversationMemory(memory, verifier),)
    )
    config = (await RunStateStore(interaction_object_store).read_run(run)).envelope.effective_agent_config
    unavailable = MemoryBehaviors(
        interaction_sessions, default=OrdinaryMemory(memory), behaviors=(ConversationMemory(memory, None),)
    )
    with pytest.raises(RunError, match="verification is unavailable"):
        await unavailable.prepare(run=run, workspace_id=WORKSPACE_ID, config=config, current_context=lambda: context)
    async with transaction(interaction_sessions) as session:
        selection = await session.get(RunMemorySelectionRecord, run.id)
        selection.binding_schema_version = 2
    with pytest.raises(RunError, match="unavailable"):
        await registry.prepare(run=run, workspace_id=WORKSPACE_ID, config=config, current_context=lambda: context)
    async with transaction(interaction_sessions) as session:
        selection = await session.get(RunMemorySelectionRecord, run.id)
        selection.binding_schema_version = 1
        selection.behavior_key = "uninstalled_behavior"
    with pytest.raises(RunError, match="unavailable"):
        await registry.prepare(run=run, workspace_id=WORKSPACE_ID, config=config, current_context=lambda: context)
    async with transaction(interaction_sessions) as session:
        await session.delete(await session.get(RunMemorySelectionRecord, run.id))
    with pytest.raises(RunError, match="missing"):
        await registry.prepare(run=run, workspace_id=WORKSPACE_ID, config=config, current_context=lambda: context)


async def test_retention_is_atomic_and_disabled_does_not_prepare_ordinary_memory(
    runtime_memory, interaction_sessions, interaction_object_store, monkeypatch
):
    from a13n_service.agents.reconstruction import AgentDefinitionReconstructionContext
    from a13n_service.bots.memory.behavior import ConversationMemory
    from a13n_service.bots.memory.bindings import RunMemoryBindingRecord
    from a13n_service.interactions.objects import RunStateStore
    from a13n_service.interactions.records import run_record
    from a13n_service.memory.behaviors import MemoryBehaviors, RunMemorySelectionRecord
    from a13n_service.memory.ordinary import OrdinaryMemory

    memory, _, run, context, _, _, binding, verifier = runtime_memory
    ordinary = OrdinaryMemory(memory)
    registry = MemoryBehaviors(
        interaction_sessions, default=ordinary, behaviors=(ConversationMemory(memory, verifier),)
    )
    from a13n_service.interactions.models import ThreadRecord

    async with transaction(interaction_sessions) as session:
        original = await session.get(ThreadRecord, run.thread_id)
        values = {c.name: getattr(original, c.name) for c in ThreadRecord.__table__.columns}
        values.update(
            id="thread_retained_memory",
            role="child",
            origin_kind="fork",
            origin_thread_id=run.thread_id,
            origin_run_id=run.id,
            current_run_id=None,
            head_run_id=None,
        )
        session.add(ThreadRecord(**values))
    successor = run.model_copy(
        update={
            "id": "run_retained_memory",
            "thread_id": "thread_retained_memory",
            "parent_run_id": run.id,
            "lineage_kind": type(run.lineage_kind).fork,
            "current_run_attempt_id": None,
            "idempotency_key": None,
        }
    )
    with pytest.raises(RuntimeError, match="abort acceptance"):
        async with transaction(interaction_sessions) as session:
            session.add(run_record(successor))
            await session.flush()
            await registry.finalize(session, successor, source_run_id=run.id)
            raise RuntimeError("abort acceptance")
    async with transaction(interaction_sessions) as session:
        assert await session.get(RunRecord, successor.id) is None
        assert await session.get(RunMemorySelectionRecord, successor.id) is None
        assert await session.get(RunMemoryBindingRecord, successor.id) is None
        session.add(run_record(successor))
        await session.flush()
        await registry.finalize(session, successor, source_run_id=run.id)
        assert await require_binding(session, successor.id) == binding

    # Fixture corruption/disable is deliberate: execution must obey the selected
    # document behavior even when an ordinary Agent configuration has memory.
    async with transaction(interaction_sessions) as session:
        stored = await session.get(RunMemoryBindingRecord, successor.id)
        stored.use_memory = False
        stored.save_on_request = False

    async def unexpected(**kwargs):
        raise AssertionError("ordinary memory must not be prepared")

    monkeypatch.setattr(ordinary, "prepare", unexpected)
    config = (await RunStateStore(interaction_object_store).read_run(run)).envelope.effective_agent_config
    prepared = await registry.prepare(
        run=successor, workspace_id=WORKSPACE_ID, config=config, current_context=lambda: context
    )
    for is_root in (True, False):
        node = AgentDefinitionReconstructionContext(
            run.agent_id, run.agent_revision_id, run.effective_agent_config_digest, is_root, config
        )
        from a13n_service.interactions.ports.memory import DisabledMemory

        assert isinstance(prepared.for_node(node), DisabledMemory)
    async with transaction(interaction_sessions) as session:
        await session.delete(await session.get(RunMemoryBindingRecord, successor.id))
    from a13n_harness.errors import RunError

    with pytest.raises(RunError, match="binding is missing"):
        await registry.prepare(run=successor, workspace_id=WORKSPACE_ID, config=config, current_context=lambda: context)


async def test_deleted_group_does_not_restore_an_old_binding(runtime_memory, interaction_sessions):
    from a13n_service.bots.memory.lifecycle import invalidate_conversation

    memory, _, run, context, document, _, binding, verifier = runtime_memory
    capability = bot_memory_capability(
        memory, run=run, binding=binding, verifier=verifier, agent_id=run.agent_id, current_context=lambda: context
    )
    async with transaction(interaction_sessions) as session:
        await invalidate_conversation(session, ACCOUNT, binding.external_conversation_id)
        scope = await session.get(ScopeRecord, binding.scope_id)
        # A new discovery/configuration may restore eligibility, never an old Run.
        scope.audience = "private"
        scope.version += 1
    assert capability is not None and capability.document_store is not None
    with pytest.raises(ApplicationError, match="unavailable"):
        await capability.document_store.read(document.id)


@pytest.mark.parametrize("kind", ["ordinary", "enabled", "disabled"])
@pytest.mark.parametrize("sealed", [False, True])
async def test_memory_cutover_preserves_run_and_binding(
    runtime_memory, interaction_sessions, interaction_object_store, service_database, kind, sealed
):
    import anyio
    from a13n_service.bots.memory.bindings import RunMemoryBindingRecord
    from a13n_service.database.migration import DatabaseMigrator
    from a13n_service.memory.behaviors import RunMemorySelectionRecord

    _, _, run, _, _, _, _, _ = runtime_memory
    if sealed:
        from a13n_harness import SafeFailure
        from a13n_service.interactions.models import ThreadRecord
        from a13n_service.interactions.objects import RunPayloadStore
        from a13n_service.interactions.outcomes import RunOutcomeService

        from tests.lifecycle_support import test_lifecycle_writer

        async with transaction(interaction_sessions) as session:
            current = await session.get(RunRecord, run.id)
            thread = await session.get(ThreadRecord, run.thread_id)
            run_version, thread_version = current.version, thread.version
        await RunOutcomeService(
            interaction_sessions, RunPayloadStore(interaction_object_store), lifecycle=test_lifecycle_writer()
        ).cancel(
            organization_id=ORGANIZATION_ID,
            run_id=run.id,
            expected_run_version=run_version,
            expected_thread_version=thread_version,
            failure=SafeFailure(code="cancelled_by_user", message="Cancelled."),
        )
    async with transaction(interaction_sessions) as session:
        stored = await session.get(RunMemoryBindingRecord, run.id)
        selection = await session.get(RunMemorySelectionRecord, run.id)
        if kind == "ordinary":
            await session.delete(stored)
            selection.behavior_key = "agent"
        elif kind == "disabled":
            stored.use_memory = stored.save_on_request = False
        expected = None if kind == "ordinary" else stored.binding()
        original = (await session.get(RunRecord, run.id)).to_resource()
    migrator = DatabaseMigrator(service_database)
    await anyio.to_thread.run_sync(migrator.downgrade, "0264713d02b1")
    await anyio.to_thread.run_sync(migrator.upgrade)
    async with transaction(interaction_sessions) as session:
        assert (await session.get(RunRecord, run.id)).to_resource() == original
        selection = await session.get(RunMemorySelectionRecord, run.id)
        assert selection.behavior_key == ("agent" if kind == "ordinary" else "bot_conversation")
        if expected is not None:
            assert await require_binding(session, run.id) == expected
        else:
            assert await session.get(RunMemoryBindingRecord, run.id) is None


async def test_invalid_old_binding_aborts_cutover(runtime_memory, interaction_sessions, service_database):
    import anyio
    from a13n_service.database.migration import DatabaseMigrator
    from sqlalchemy import text

    migrator = DatabaseMigrator(service_database)
    await anyio.to_thread.run_sync(migrator.downgrade, "0264713d02b1")
    async with transaction(interaction_sessions) as session:
        await session.execute(text("UPDATE runs SET bot_memory_json = :invalid"), {"invalid": "[]"})
    with pytest.raises(RuntimeError, match="Invalid retained memory"):
        await anyio.to_thread.run_sync(migrator.upgrade)
    async with transaction(interaction_sessions) as session:
        assert (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == "0264713d02b1"


async def test_cutover_downgrade_preserves_revocation_fences(runtime_memory, interaction_sessions, service_database):
    import anyio
    from a13n_service.bots.memory.lifecycle import invalidate_conversation
    from a13n_service.database.migration import DatabaseMigrator
    from sqlalchemy import text

    _, _, _, _, _, _, binding, _ = runtime_memory
    async with transaction(interaction_sessions) as session:
        await invalidate_conversation(session, ACCOUNT, binding.external_conversation_id)
    with pytest.raises(RuntimeError, match="revocation fences cannot be downgraded"):
        await anyio.to_thread.run_sync(DatabaseMigrator(service_database).downgrade, "4662868f6a0f")
    async with transaction(interaction_sessions) as session:
        scope = await session.get(ScopeRecord, binding.scope_id)
        assert scope.binding_floor > binding.scope_version
        assert (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == "eb41d745e5d0"
