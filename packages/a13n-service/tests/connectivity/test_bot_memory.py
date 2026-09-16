"""Bot document behavior through real SQL and the native OSS adapter boundary."""

import json
from dataclasses import dataclass
from uuid import uuid4

import httpx2
import pytest
from a13n_harness.capabilities.mem0_backends import Mem0OSSBackend
from a13n_service.application_errors import ApplicationError
from a13n_service.bots.memory.domain import ConfigureScope, CreateDocument, SearchDocuments
from a13n_service.bots.memory.mutations import create, delete
from a13n_service.bots.memory.service import BotMemoryService
from a13n_service.bots.memory.settings import (
    AccountSettingsRecord,
    ReplaceMemorySettings,
    read_settings,
    replace_settings,
)
from a13n_service.connectivity.accounts.targets import TargetConfig
from a13n_service.storage import transaction

from ..memory.support import memory_service
from .conftest import ACCOUNT_ID, WORKSPACE_ID, actor

pytestmark = pytest.mark.anyio


async def test_uncertain_write_reconciles_without_repeating_add(bot_memory):
    from a13n_service.bots.memory.operations import list_operations, reconcile

    lab = bot_memory
    lab.failures["get"] = True
    body = CreateDocument(text="Saved remotely, response lost", title="Unconfirmed")
    with pytest.raises(ApplicationError) as uncertain:
        await create(lab.service, actor(), ACCOUNT_ID, lab.scope_id, body, "uncertain")
    assert uncertain.value.code == "memory_write_unconfirmed"
    assert len(lab.records) == 1
    assert not (await lab.service.index(actor(), ACCOUNT_ID, lab.scope_id)).entries
    operations = await list_operations(lab.service, actor(), ACCOUNT_ID, lab.scope_id)
    assert len(operations.items) == 1 and operations.items[0].state == "unconfirmed"
    with pytest.raises(ApplicationError):
        await create(lab.service, actor(), ACCOUNT_ID, lab.scope_id, body, "uncertain")
    lab.failures["get"] = False
    lab.failures["search_empty"] = True
    still_uncertain = await reconcile(lab.service, actor(), ACCOUNT_ID, lab.scope_id, operations.items[0].id)
    assert still_uncertain.state == "unconfirmed"
    lab.failures["search_empty"] = False
    confirmed = await reconcile(lab.service, actor(), ACCOUNT_ID, lab.scope_id, operations.items[0].id)
    assert confirmed.state == "active"
    replay = await create(lab.service, actor(), ACCOUNT_ID, lab.scope_id, body, "uncertain")
    assert replay.id == confirmed.id
    assert len([call for call in lab.calls if call == ("POST", "/memories")]) == 1


async def test_withdrawal_is_terminal_and_keeps_source(bot_memory, target_service, connectivity_sessions):
    from a13n_service.bots.memory.domain import PublicationAudience, PublishDocument
    from a13n_service.bots.memory.operations import operation
    from a13n_service.bots.memory.publications import audience, get_audience, publish, withdraw

    lab = bot_memory
    recipient = await sharing_groups(lab, target_service, connectivity_sessions)
    source = await create(
        lab.service, actor(), ACCOUNT_ID, lab.scope_id, CreateDocument(text="Source", title="Source"), "source"
    )
    published = await publish(
        lab.service,
        actor(),
        ACCOUNT_ID,
        lab.scope_id,
        source.id,
        PublishDocument(text="Approved", title="Copy", recipient_scope_ids=(recipient.id,)),
        "copy",
    )
    access = await get_audience(lab.service, actor(), ACCOUNT_ID, lab.scope_id, published.id)
    assert access.recipient_scope_ids == (recipient.id,)
    await withdraw(lab.service, actor(), ACCOUNT_ID, lab.scope_id, published.id, access.version)
    await withdraw(lab.service, actor(), ACCOUNT_ID, lab.scope_id, published.id, access.version)
    assert (await operation(lab.service, actor(), ACCOUNT_ID, lab.scope_id, published.id)).state == "deleted"
    assert not (await lab.service.index(actor(), ACCOUNT_ID, recipient.id)).entries
    assert (await lab.service.get(actor(), ACCOUNT_ID, lab.scope_id, source.id)).text == "Source"
    with pytest.raises(ApplicationError):
        await audience(
            lab.service,
            actor(),
            ACCOUNT_ID,
            lab.scope_id,
            published.id,
            PublicationAudience(expected_version=access.version + 1, recipient_scope_ids=(recipient.id,)),
        )


async def test_source_delete_fences_unconfirmed_publication_during_reconciliation(
    bot_memory,
    target_service,
    connectivity_sessions,
):
    from a13n_service.bots.memory.domain import PublishDocument
    from a13n_service.bots.memory.operations import list_operations, reconcile
    from a13n_service.bots.memory.publications import publish

    lab = bot_memory
    recipient = await sharing_groups(lab, target_service, connectivity_sessions)
    source = await create(
        lab.service, actor(), ACCOUNT_ID, lab.scope_id, CreateDocument(text="Source", title="Source"), "source"
    )
    # Publish reads its source first, then creates the remote copy. Fail only the copy readback.
    original_get = lab.service.get

    async def read_source(*args, **kwargs):
        result = await original_get(*args, **kwargs)
        lab.failures["get"] = True
        return result

    lab.service.get = read_source
    with pytest.raises(ApplicationError):
        await publish(
            lab.service,
            actor(),
            ACCOUNT_ID,
            lab.scope_id,
            source.id,
            PublishDocument(text="Approved", title="Copy", recipient_scope_ids=(recipient.id,)),
            "copy",
        )
    lab.service.get = original_get
    lab.failures["get"] = False
    copies = await list_operations(lab.service, actor(), ACCOUNT_ID, lab.scope_id)
    assert len(copies.items) == 1 and copies.items[0].publication_source_id == source.id
    with pytest.raises(ApplicationError):
        await delete(lab.service, actor(), ACCOUNT_ID, lab.scope_id, source.id)
    repaired = await reconcile(lab.service, actor(), ACCOUNT_ID, lab.scope_id, copies.items[0].id)
    assert repaired.state == "deleting", "Reconciliation must never republish a deleted source"
    assert not (await lab.service.index(actor(), ACCOUNT_ID, recipient.id)).entries
    await delete(lab.service, actor(), ACCOUNT_ID, lab.scope_id, source.id)
    assert not lab.records


@dataclass
class MemoryLab:
    service: BotMemoryService
    scope_id: str
    records: dict
    calls: list
    failures: dict


@pytest.fixture
async def bot_memory(connectivity_sessions, account_service, target_service):
    records, calls, failures = {}, [], {}

    def handle(request):
        body = json.loads(request.content) if request.content else {}
        calls.append((request.method, request.url.path))
        if request.url.path == "/memories" and request.method == "POST":
            key = str(uuid4())
            records[key] = {
                "id": key,
                "memory": body["messages"][0]["content"],
                "run_id": body["run_id"],
                "metadata": body["metadata"],
            }
            return httpx2.Response(200, json={"results": [{"id": key, "event": "ADD"}]})
        if request.method == "GET" and failures.get("get"):
            return httpx2.Response(503, json={"detail": "synthetic readback failure"})
        if request.url.path == "/search":
            if failures.get("search_empty"):
                return httpx2.Response(200, json={"results": []})
            result = [
                dict(record, score=0.8)
                for record in records.values()
                if record["run_id"] == body["filters"]["run_id"]
                and record["metadata"]["record_key"] in body["filters"]["record_key"]["in"]
            ]
            return httpx2.Response(200, json={"results": result[: body["top_k"]]})
        key = request.url.path.split("/")[-1]
        if request.method == "DELETE":
            records.pop(key, None)
            return httpx2.Response(200, json={"message": "deleted"})
        return httpx2.Response(200, content=json.dumps(records.get(key)), headers={"content-type": "application/json"})

    async with httpx2.AsyncClient(base_url="http://mem0/", transport=httpx2.MockTransport(handle)) as client:
        memory, provider, _ = await memory_service(
            connectivity_sessions, Mem0OSSBackend(client), principal=actor(), workspace_id=WORKSPACE_ID
        )
        async with transaction(connectivity_sessions) as session:
            session.add(
                AccountSettingsRecord(
                    account_id=ACCOUNT_ID,
                    version=1,
                    provider_id=provider.id,
                    use_memory=True,
                    save_on_request=True,
                    timezone="UTC",
                )
            )
        await target_service.create(
            actor=actor(),
            account_id=ACCOUNT_ID,
            idempotency_key="memory-target",
            request=TargetConfig(target_kind="conversation", external_target_id="engineering"),
        )
        service = BotMemoryService(memory)
        scope = await service.configure_scope(
            actor(), ACCOUNT_ID, ConfigureScope(external_conversation_id="engineering")
        )
        yield MemoryLab(service, scope.id, records, calls, failures)


async def test_index_is_navigation_only_and_body_is_read_on_demand(bot_memory):
    lab = bot_memory
    document = await create(
        lab.service,
        actor(),
        ACCOUNT_ID,
        lab.scope_id,
        CreateDocument(text="# Release\nThe full release procedure.", title="Release", description="Deployment steps"),
        "doc-one",
    )
    lab.calls.clear()
    index = await lab.service.index(actor(), ACCOUNT_ID, lab.scope_id)
    assert document.id in index.text and "Deployment steps" in index.text
    assert "The full release procedure" not in index.text
    assert lab.calls == []
    read = await lab.service.get(actor(), ACCOUNT_ID, lab.scope_id, document.id)
    assert read.text == document.text
    assert len(lab.calls) == 1
    result = await lab.service.search(actor(), ACCOUNT_ID, lab.scope_id, SearchDocuments(query="release"))
    assert [entry.id for entry in result.items] == [document.id]


async def test_directory_pagination_date_filter_and_request_replay(bot_memory):
    from datetime import date

    lab = bot_memory
    for index in range(3):
        body = CreateDocument(text=f"Memory {index}", title=f"Topic {index}", activity_date=date(2026, 9, 15))
        first = await create(lab.service, actor(), ACCOUNT_ID, lab.scope_id, body, str(index))
        again = await create(lab.service, actor(), ACCOUNT_ID, lab.scope_id, body, str(index))
        assert again.id == first.id
    assert len(lab.records) == 3
    first = await lab.service.list(actor(), ACCOUNT_ID, lab.scope_id, limit=2, activity_date=date(2026, 9, 15))
    second = await lab.service.list(
        actor(), ACCOUNT_ID, lab.scope_id, limit=2, activity_date=date(2026, 9, 15), cursor=first.next_cursor
    )
    assert len(first.items) == 2 and len(second.items) == 1 and second.next_cursor is None
    assert not {entry.id for entry in first.items} & {entry.id for entry in second.items}
    with pytest.raises(ApplicationError) as mismatch:
        await lab.service.list(
            actor(), ACCOUNT_ID, lab.scope_id, limit=2, activity_date=date(2026, 9, 16), cursor=first.next_cursor
        )
    assert mismatch.value.code == "invalid_cursor"
    with pytest.raises(ApplicationError) as reused:
        await create(lab.service, actor(), ACCOUNT_ID, lab.scope_id, CreateDocument(text="changed", title="Topic"), "0")
    assert reused.value.code == "idempotency_conflict"


async def test_deleted_document_disappears_and_stale_reference_cannot_read(bot_memory):
    lab = bot_memory
    document = await create(
        lab.service, actor(), ACCOUNT_ID, lab.scope_id, CreateDocument(text="Old fact", title="Fact"), "old"
    )
    await delete(lab.service, actor(), ACCOUNT_ID, lab.scope_id, document.id)
    assert not lab.records
    assert (await lab.service.index(actor(), ACCOUNT_ID, lab.scope_id)).entries == ()
    await delete(lab.service, actor(), ACCOUNT_ID, lab.scope_id, document.id)
    with pytest.raises(ApplicationError) as missing:
        await lab.service.get(actor(), ACCOUNT_ID, lab.scope_id, document.id)
    assert missing.value.code == "memory_not_found"


async def test_provider_body_tampering_is_not_returned_as_saved_memory(bot_memory):
    lab = bot_memory
    document = await create(
        lab.service, actor(), ACCOUNT_ID, lab.scope_id, CreateDocument(text="Original", title="Fact"), "one"
    )
    next(iter(lab.records.values()))["memory"] = "Rewritten outside Service"
    with pytest.raises(ApplicationError) as changed:
        await lab.service.get(actor(), ACCOUNT_ID, lab.scope_id, document.id)
    assert changed.value.code == "memory_unavailable"


async def sharing_groups(lab, target_service, sessions):
    from a13n_service.bots.memory.models import ScopeRecord
    from a13n_service.storage import transaction
    from sqlalchemy import update

    await target_service.create(
        actor=actor(),
        account_id=ACCOUNT_ID,
        idempotency_key="recipient",
        request=TargetConfig(target_kind="conversation", external_target_id="support"),
    )
    recipient = await lab.service.configure_scope(
        actor(), ACCOUNT_ID, ConfigureScope(external_conversation_id="support")
    )
    async with transaction(sessions) as session:
        await session.execute(
            update(ScopeRecord).where(ScopeRecord.id.in_((lab.scope_id, recipient.id))).values(audience="private")
        )
    return recipient


async def test_publication_is_separate_immutable_content_and_source_delete_withdraws_all(
    bot_memory, target_service, connectivity_sessions
):
    from a13n_service.bots.memory.domain import PublishDocument
    from a13n_service.bots.memory.publications import publish

    lab = bot_memory
    recipient = await sharing_groups(lab, target_service, connectivity_sessions)
    source = await create(
        lab.service,
        actor(),
        ACCOUNT_ID,
        lab.scope_id,
        CreateDocument(text="Private evidence and useful conclusion", title="Decision"),
        "source",
    )
    assert (await lab.service.list(actor(), ACCOUNT_ID, recipient.id)).items == ()
    published = await publish(
        lab.service,
        actor(),
        ACCOUNT_ID,
        lab.scope_id,
        source.id,
        PublishDocument(text="Useful conclusion", title="Shared decision", recipient_scope_ids=(recipient.id,)),
        "publication",
    )
    received = await lab.service.get(actor(), ACCOUNT_ID, recipient.id, published.id)
    assert received.text == "Useful conclusion" and received.shared
    assert received.publication_source_id is None
    assert received.owner_name and [r.kind for r in received.access_reasons] == ["publication"]
    with pytest.raises(ApplicationError):
        await lab.service.get(actor(), ACCOUNT_ID, recipient.id, source.id)
    assert len(lab.records) == 2
    await delete(lab.service, actor(), ACCOUNT_ID, lab.scope_id, source.id)
    assert not lab.records
    assert (await lab.service.index(actor(), ACCOUNT_ID, recipient.id)).entries == ()
    with pytest.raises(ApplicationError):
        await lab.service.get(actor(), ACCOUNT_ID, recipient.id, published.id)


async def test_group_policy_defaults_exclude_history_and_leave_revokes_access(
    bot_memory, target_service, connectivity_sessions
):
    from a13n_service.bots.memory.domain import ReplaceSharingPolicy, SharingPolicyInput
    from a13n_service.bots.memory.sharing import save_policy

    lab = bot_memory
    recipient = await sharing_groups(lab, target_service, connectivity_sessions)
    old = await create(lab.service, actor(), ACCOUNT_ID, lab.scope_id, CreateDocument(text="Past", title="Old"), "old")
    policy = await save_policy(
        lab.service, actor(), ACCOUNT_ID, SharingPolicyInput(name="Knowledge", scope_ids=(lab.scope_id, recipient.id))
    )
    fresh = await create(lab.service, actor(), ACCOUNT_ID, lab.scope_id, CreateDocument(text="New", title="New"), "new")
    visible = await lab.service.list(actor(), ACCOUNT_ID, recipient.id)
    assert {item.id for item in visible.items} == {fresh.id}
    assert old.id not in {item.id for item in visible.items}
    await save_policy(
        lab.service,
        actor(),
        ACCOUNT_ID,
        ReplaceSharingPolicy(
            **policy.model_dump(exclude={"id", "version", "created_at", "future_since", "participants", "enabled"}),
            expected_version=policy.version,
            enabled=False,
        ),
        policy.id,
    )
    assert (await lab.service.list(actor(), ACCOUNT_ID, recipient.id)).items == ()


async def test_viewer_cannot_read_index_or_provider_body(bot_memory, connectivity_sessions):
    from a13n_service.iam import AuthorizationError
    from a13n_service.iam.models import RoleBindingRecord
    from a13n_service.storage import transaction
    from sqlalchemy import update

    from .conftest import USER_ID

    async with transaction(connectivity_sessions) as session:
        await session.execute(
            update(RoleBindingRecord)
            .where(RoleBindingRecord.principal_id == USER_ID, RoleBindingRecord.workspace_id == WORKSPACE_ID)
            .values(role_key="viewer")
        )
    bot_memory.calls.clear()
    with pytest.raises(AuthorizationError):
        await bot_memory.service.index(actor(), ACCOUNT_ID, bot_memory.scope_id)
    assert bot_memory.calls == []


async def test_builder_cannot_change_or_remove_bot_memory_settings(bot_memory, account_service, connectivity_sessions):
    from a13n_service.connectivity.accounts.models import AccountRecord
    from a13n_service.iam import AuthorizationError
    from a13n_service.iam.models import RoleBindingRecord
    from a13n_service.storage import transaction
    from sqlalchemy import update

    from .conftest import USER_ID

    async with transaction(connectivity_sessions) as session:
        row = await session.get(AccountRecord, ACCOUNT_ID)
        row.provider_key = "slack"
        settings = await read_settings(session, ACCOUNT_ID)
    async with transaction(connectivity_sessions) as session:
        await session.execute(
            update(RoleBindingRecord)
            .where(
                RoleBindingRecord.principal_id == USER_ID,
                RoleBindingRecord.workspace_id == WORKSPACE_ID,
            )
            .values(role_key="builder")
        )
    for value in (None, settings.memory.model_copy(update={"use_memory": False})):
        with pytest.raises(AuthorizationError):
            await replace_settings(
                bot_memory.service.memory,
                actor(),
                ACCOUNT_ID,
                ReplaceMemorySettings(expected_version=settings.version, memory=value),
            )
    async with transaction(connectivity_sessions) as session:
        assert await read_settings(session, ACCOUNT_ID) == settings


async def test_audit_records_lifecycle_without_document_content(bot_memory, connectivity_sessions):
    from a13n_service.iam.models import SecurityAuditRecord
    from sqlalchemy import select

    document = await create(
        bot_memory.service,
        actor(),
        ACCOUNT_ID,
        bot_memory.scope_id,
        CreateDocument(text="Private audit evidence", title="Private title"),
        "audit",
    )
    await delete(bot_memory.service, actor(), ACCOUNT_ID, bot_memory.scope_id, document.id)
    async with connectivity_sessions() as session:
        rows = list(
            await session.scalars(select(SecurityAuditRecord).where(SecurityAuditRecord.resource_id == document.id))
        )
        assert {row.action for row in rows} == {
            "bot_memory.create",
            "bot_memory.delete_requested",
            "bot_memory.delete_confirmed",
        }
        assert all(row.actor_id == actor().principal.principal_id for row in rows)
        assert "Private audit evidence" not in repr([row.details for row in rows])
        assert "Private title" not in repr([row.details for row in rows])


async def test_group_configuration_uses_verified_metadata_and_target_recreation_requires_a_new_check(
    bot_memory, target_service, connectivity_sessions
):
    from a13n_service.bots.connectivity.domain import BotCheck
    from a13n_service.bots.connectivity.models import BotCheckRecord
    from a13n_service.bots.memory.models import ScopeRecord
    from a13n_service.connectivity.accounts.models import AccountRecord
    from a13n_service.connectivity.accounts.target_models import AccountTargetRecord
    from a13n_service.connectivity.inspection import ConversationInfo, InstallationInfo
    from a13n_service.storage import transaction
    from a13n_service.temporal import utc_now
    from sqlalchemy import select

    lab = bot_memory
    document = await create(
        lab.service,
        actor(),
        ACCOUNT_ID,
        lab.scope_id,
        CreateDocument(text="Retained evidence", title="Retained"),
        "retained",
    )
    async with transaction(connectivity_sessions) as session:
        account = await session.get(AccountRecord, ACCOUNT_ID)
        current = await session.get(ScopeRecord, lab.scope_id)
        initial_version = current.version
        checked = BotCheck(
            account_id=ACCOUNT_ID,
            credential_generation=account.credential_generation,
            checked_at=utc_now(),
            conversation_id="engineering",
            installation=InstallationInfo(
                app_id="A1",
                organization_id="T1",
                organization_name="Acme",
                bot_id="U1",
                bot_name="Helper",
                enabled=True,
            ),
            conversation=ConversationInfo(
                id="engineering",
                name="Engineering team",
                audience="private",
                is_member=True,
                is_active=True,
                external=False,
            ),
        )
        session.add(
            BotCheckRecord(
                account_id=ACCOUNT_ID,
                conversation_id="engineering",
                credential_generation=account.credential_generation,
                started_at=utc_now(),
                result_json=checked.model_dump(mode="json"),
            )
        )
    configured = await lab.service.configure_scope(
        actor(), ACCOUNT_ID, ConfigureScope(external_conversation_id="engineering", expected_version=initial_version)
    )
    assert configured.name == "Engineering team" and configured.audience == "private"
    async with transaction(connectivity_sessions) as session:
        target = await session.scalar(
            select(AccountTargetRecord).where(
                AccountTargetRecord.account_id == ACCOUNT_ID, AccountTargetRecord.external_target_id == "engineering"
            )
        )
        target_id, version = target.id, target.version
    await target_service.delete(actor=actor(), account_id=ACCOUNT_ID, target_id=target_id, expected_version=version)
    async with transaction(connectivity_sessions) as session:
        current = await session.get(ScopeRecord, lab.scope_id)
        assert current.audience == "unknown" and current.version == configured.version + 1
        assert await session.get(BotCheckRecord, (ACCOUNT_ID, "engineering")) is None
        invalidated_version = current.version
    await target_service.create(
        actor=actor(),
        account_id=ACCOUNT_ID,
        idempotency_key="recreated-target",
        request=TargetConfig(target_kind="conversation", external_target_id="engineering"),
    )
    configured = await lab.service.configure_scope(
        actor(),
        ACCOUNT_ID,
        ConfigureScope(external_conversation_id="engineering", expected_version=invalidated_version),
    )
    assert configured.audience == "unknown", "An old setup check cannot re-enable a recreated target"
    assert (await lab.service.get(actor(), ACCOUNT_ID, lab.scope_id, document.id)).text == "Retained evidence"


async def test_exact_target_scope_lookup_is_bounded_and_uses_no_provider_io(
    bot_memory, target_service, connectivity_sessions
):
    from a13n_service.bots.memory.models import ScopeRecord
    from a13n_service.connectivity.accounts.target_models import AccountTargetRecord
    from a13n_service.storage import short_session
    from sqlalchemy import select

    lab = bot_memory
    recipient = await sharing_groups(lab, target_service, connectivity_sessions)
    async with short_session(connectivity_sessions) as session:
        source = await session.get(ScopeRecord, lab.scope_id)
        target = await session.scalar(
            select(AccountTargetRecord).where(
                AccountTargetRecord.account_id == ACCOUNT_ID, AccountTargetRecord.external_target_id == "support"
            )
        )
        provider_id, target_id = source.provider_id, target.id
    lab.calls.clear()
    result = await lab.service.scopes(actor(), ACCOUNT_ID, provider_id, target_id=target_id, limit=1)
    assert [scope.id for scope in result.items] == [recipient.id] and result.next_cursor is None
    assert lab.calls == []
    unfiltered = await lab.service.scopes(actor(), ACCOUNT_ID, provider_id, limit=1)
    with pytest.raises(ApplicationError):
        await lab.service.scopes(
            actor(), ACCOUNT_ID, provider_id, target_id=target_id, limit=1, cursor=unfiltered.next_cursor
        )
    unconfigured = await target_service.create(
        actor=actor(),
        account_id=ACCOUNT_ID,
        idempotency_key="no-scope",
        request=TargetConfig(target_kind="conversation", external_target_id="no-memory"),
    )
    assert not (await lab.service.scopes(actor(), ACCOUNT_ID, provider_id, target_id=unconfigured.id)).items
    await target_service.delete(
        actor=actor(), account_id=ACCOUNT_ID, target_id=target_id, expected_version=target.version
    )
    with pytest.raises(ApplicationError) as missing:
        await lab.service.scopes(actor(), ACCOUNT_ID, provider_id, target_id=target_id)
    assert missing.value.code == "target_not_found"


async def test_scope_lookup_keeps_admin_and_account_deletion_boundaries(
    bot_memory, connectivity_sessions, account_service
):
    from a13n_service.bots.memory.models import ScopeRecord
    from a13n_service.iam import AuthorizationError
    from a13n_service.iam.models import RoleBindingRecord
    from a13n_service.storage import transaction

    lab = bot_memory
    async with transaction(connectivity_sessions) as session:
        provider_id = (await session.get(ScopeRecord, lab.scope_id)).provider_id
        (await session.get(RoleBindingRecord, "rb_connectivity_admin")).role_key = "viewer"
    with pytest.raises(AuthorizationError):
        await lab.service.scopes(actor(), ACCOUNT_ID, provider_id)
    async with transaction(connectivity_sessions) as session:
        (await session.get(RoleBindingRecord, "rb_connectivity_admin")).role_key = "admin"
    account = await account_service.get_account(actor=actor(), account_id=ACCOUNT_ID)
    await account_service.delete_account(actor=actor(), account_id=ACCOUNT_ID, expected_version=account.version)
    with pytest.raises(ApplicationError) as deleted:
        await lab.service.scopes(actor(), ACCOUNT_ID, provider_id)
    assert deleted.value.code == "account_not_found"


async def test_index_pages_by_encoded_budget_without_skipping_documents(bot_memory):
    lab = bot_memory
    created = []
    for number in range(23):
        document = await create(
            lab.service,
            actor(),
            ACCOUNT_ID,
            lab.scope_id,
            CreateDocument(
                text=f"Body {number}",
                title="<" * 160,
                description="&" * 320,
            ),
            f"encoded-index-{number}",
        )
        created.append(document.id)
    lab.calls.clear()
    seen, cursors = [], set()
    cursor = None
    while True:
        page = await lab.service.index(actor(), ACCOUNT_ID, lab.scope_id, cursor=cursor)
        payload = json.dumps({"text": page.text, "next_cursor": page.next_cursor}, ensure_ascii=False)
        payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        assert len(payload.encode()) < 32 * 1024
        assert 1 <= len(page.entries) <= 20
        for entry in page.entries:
            assert f"memory://{entry.id}" in page.text
            assert entry.title == "<" * 160 and entry.description == "&" * 320
            seen.append(entry.id)
        assert "Body " not in page.text
        if not page.next_cursor:
            break
        assert "partial index" in page.text
        assert page.next_cursor not in cursors
        cursors.add(page.next_cursor)
        cursor = page.next_cursor
    assert seen == sorted(created)
    assert len(cursors) >= 2, "Encoded size must paginate before the twenty-entry limit"
    assert not lab.calls, "Index navigation must not load provider bodies"


@pytest.mark.parametrize("operation", ["create", "configure", "account"])
async def test_unsupported_provider_is_rejected_before_any_document_operation(bot_memory, account_service, operation):
    from a13n_service.bots.memory.operations import list_operations

    lab = bot_memory
    plugin = next(iter(lab.service.memory.catalog.values()))
    plugin.supports_documents = False
    opened = plugin.opened
    with pytest.raises(ApplicationError) as denied:
        if operation == "create":
            await create(
                lab.service,
                actor(),
                ACCOUNT_ID,
                lab.scope_id,
                CreateDocument(title="Unsupported", text="Never dispatched"),
                "unsupported",
            )
        elif operation == "configure":
            await lab.service.configure_scope(
                actor(), ACCOUNT_ID, ConfigureScope(external_conversation_id="engineering", expected_version=1)
            )
        else:
            from a13n_service.connectivity.accounts.models import AccountRecord

            async with transaction(lab.service.sessions) as session:
                account = await session.get(AccountRecord, ACCOUNT_ID)
                account.provider_key = "slack"
                settings = await read_settings(session, ACCOUNT_ID)
            await replace_settings(
                lab.service.memory,
                actor(),
                ACCOUNT_ID,
                ReplaceMemorySettings(expected_version=settings.version, memory=settings.memory),
            )
    assert denied.value.code == "memory_documents_unsupported"
    assert plugin.opened == opened and not lab.records
    assert not (await list_operations(lab.service, actor(), ACCOUNT_ID, lab.scope_id)).items


async def test_detail_explains_only_current_policy_grants(bot_memory, target_service, connectivity_sessions):
    from a13n_service.bots.memory.domain import ReplaceSharingPolicy, SharingPolicyInput
    from a13n_service.bots.memory.sharing import save_policy

    lab = bot_memory
    recipient = await sharing_groups(lab, target_service, connectivity_sessions)
    saved = await create(
        lab.service, actor(), ACCOUNT_ID, lab.scope_id, CreateDocument(text="Fact", title="Note"), "reason"
    )
    first, second, future = [
        await save_policy(
            lab.service,
            actor(),
            ACCOUNT_ID,
            SharingPolicyInput(
                name=name,
                scope_ids=(lab.scope_id, recipient.id),
                include_history=history,
            ),
        )
        for name, history in (("Engineering", True), ("Support", True), ("Future", False))
    ]
    local = await lab.service.get(actor(), ACCOUNT_ID, lab.scope_id, saved.id)
    assert [r.kind for r in local.access_reasons] == ["owner"] and local.owner_name
    received = await lab.service.get(actor(), ACCOUNT_ID, recipient.id, saved.id)
    assert {r.policy_id: r.policy_name for r in received.access_reasons} == {
        first.id: "Engineering",
        second.id: "Support",
    }
    assert all(r.kind == "policy" for r in received.access_reasons)
    assert not received.more_access_reasons and received.owner_name == local.owner_name
    assert future.id not in {r.policy_id for r in received.access_reasons}
    for policy in (first, second):
        await save_policy(
            lab.service,
            actor(),
            ACCOUNT_ID,
            ReplaceSharingPolicy(
                name=policy.name,
                scope_ids=policy.scope_ids,
                include_history=True,
                expected_version=policy.version,
                enabled=False,
            ),
            policy.id,
        )
        if policy.id == first.id:
            remaining = await lab.service.get(actor(), ACCOUNT_ID, recipient.id, saved.id)
            assert [(r.policy_id, r.policy_name) for r in remaining.access_reasons] == [(second.id, "Support")]
    with pytest.raises(ApplicationError):
        await lab.service.get(actor(), ACCOUNT_ID, recipient.id, saved.id)


async def test_detail_bounds_policy_explanations(bot_memory, target_service, connectivity_sessions):
    from a13n_service.bots.memory.domain import SharingPolicyInput
    from a13n_service.bots.memory.sharing import save_policy

    lab = bot_memory
    recipient = await sharing_groups(lab, target_service, connectivity_sessions)
    saved = await create(
        lab.service,
        actor(),
        ACCOUNT_ID,
        lab.scope_id,
        CreateDocument(text="Fact", title="Bounded reasons"),
        "bounded-reasons",
    )
    policies = [
        await save_policy(
            lab.service,
            actor(),
            ACCOUNT_ID,
            SharingPolicyInput(
                name=f"Policy {i}",
                scope_ids=(lab.scope_id, recipient.id),
                include_history=True,
            ),
        )
        for i in range(21)
    ]
    detail = await lab.service.get(actor(), ACCOUNT_ID, recipient.id, saved.id)
    assert detail.more_access_reasons and len(detail.access_reasons) == 20
    assert [r.policy_id for r in detail.access_reasons] == sorted(p.id for p in policies)[:20]
