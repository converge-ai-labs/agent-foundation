"""Group visibility is one-way, installation-bounded, and immediately revocable."""

import pytest
from a13n_service.application_errors import ApplicationError
from a13n_service.bots.connectivity.domain import BotCheck
from a13n_service.bots.connectivity.models import BotCheckRecord
from a13n_service.bots.memory.domain import ConfigureScope, CreateDocument, ScopeSettings, SearchDocuments
from a13n_service.bots.memory.models import ScopeRecord
from a13n_service.bots.memory.mutations import create, delete
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.accounts.target_models import AccountTargetRecord
from a13n_service.connectivity.accounts.targets import TargetConfig
from a13n_service.connectivity.inspection import ConversationInfo, InstallationInfo
from a13n_service.storage import transaction
from a13n_service.temporal import utc_now
from sqlalchemy import select

from .conftest import ACCOUNT_ID, actor
from .test_bot_memory import bot_memory as bot_memory

pytestmark = pytest.mark.anyio


async def group(lab, targets, sessions, name, *, audience="private", member=True, active=True):
    await targets.create(
        actor=actor(),
        account_id=ACCOUNT_ID,
        idempotency_key=name,
        request=TargetConfig(target_kind="conversation", external_target_id=name),
    )
    async with transaction(sessions) as session:
        account = await session.get(AccountRecord, ACCOUNT_ID)
        check = BotCheck(
            account_id=ACCOUNT_ID,
            credential_generation=account.credential_generation,
            checked_at=utc_now(),
            conversation_id=name,
            installation=InstallationInfo(
                app_id="A1",
                organization_id="T1",
                organization_name="Acme",
                bot_id="U1",
                bot_name="Helper",
                enabled=True,
            ),
            conversation=ConversationInfo(
                id=name, name=name, audience=audience, is_member=member, is_active=active, external=False
            ),
        )
        session.add(
            BotCheckRecord(
                account_id=ACCOUNT_ID,
                conversation_id=name,
                credential_generation=account.credential_generation,
                started_at=utc_now(),
                result_json=check.model_dump(mode="json"),
            )
        )
    return await lab.service.configure_scope(actor(), ACCOUNT_ID, ConfigureScope(external_conversation_id=name))


async def visibility(lab, scope, value):
    return await lab.service.configure_scope(
        actor(),
        ACCOUNT_ID,
        ConfigureScope(
            external_conversation_id=scope.external_conversation_id,
            expected_version=scope.version,
            visibility=value,
        ),
    )


async def test_visibility_includes_history_new_documents_and_future_groups_without_copying(
    bot_memory, target_service, connectivity_sessions
):
    lab = bot_memory
    source = await group(lab, target_service, connectivity_sessions, "product")
    receiver = await group(lab, target_service, connectivity_sessions, "support")
    old = await create(
        lab.service,
        actor(),
        ACCOUNT_ID,
        source.id,
        CreateDocument(kind="semantic", text="Historical decision", title="Old"),
        "old",
    )
    private = await create(
        lab.service,
        actor(),
        ACCOUNT_ID,
        receiver.id,
        CreateDocument(kind="semantic", text="Private support", title="Private"),
        "private",
    )
    assert source.visibility == "group"
    assert {x.id for x in (await lab.service.list(actor(), ACCOUNT_ID, receiver.id)).items} == {private.id}
    source = await visibility(lab, source, "installation")
    fresh = await create(
        lab.service,
        actor(),
        ACCOUNT_ID,
        source.id,
        CreateDocument(text="Daily result", title="Daily", kind="episodic"),
        "new",
    )
    later = await group(lab, target_service, connectivity_sessions, "later")
    for recipient in (receiver, later):
        ids = {x.id for x in (await lab.service.index(actor(), ACCOUNT_ID, recipient.id)).entries}
        assert {old.id, fresh.id} <= ids
        assert {
            x.id
            for x in (
                await lab.service.search(actor(), ACCOUNT_ID, recipient.id, SearchDocuments(query="decision"))
            ).items
        } >= {old.id, fresh.id}
        doc = await lab.service.get(actor(), ACCOUNT_ID, recipient.id, old.id)
        assert doc.shared and doc.owner_name == "product"
        assert [x.kind for x in doc.access_reasons] == ["installation"]
        with pytest.raises(ApplicationError):
            await delete(lab.service, actor(), ACCOUNT_ID, recipient.id, old.id)
    assert private.id not in {x.id for x in (await lab.service.index(actor(), ACCOUNT_ID, source.id)).entries}
    assert len(lab.records) == 3, "Visibility never creates Provider copies"
    assert not (await lab.service.list(actor(), ACCOUNT_ID, later.id, include_shared=False)).items
    source = await visibility(lab, source, "group")
    assert not (await lab.service.index(actor(), ACCOUNT_ID, later.id)).entries
    assert not (await lab.service.search(actor(), ACCOUNT_ID, later.id, SearchDocuments(query="decision"))).items
    before = len(lab.calls)
    with pytest.raises(ApplicationError) as error:
        await lab.service.get(actor(), ACCOUNT_ID, later.id, old.id)
    assert error.value.code == "memory_not_found" and len(lab.calls) == before
    assert (await lab.service.get(actor(), ACCOUNT_ID, source.id, old.id)).text == "Historical decision"


@pytest.mark.parametrize("audience", ["direct", "unknown"])
async def test_only_verified_groups_can_open_memory(bot_memory, target_service, connectivity_sessions, audience):
    scope = await group(bot_memory, target_service, connectivity_sessions, "restricted", audience=audience)
    with pytest.raises(ApplicationError) as error:
        await visibility(bot_memory, scope, "installation")
    assert error.value.code == "memory_visibility_unavailable"
    # A missing setting on older persisted scopes remains private.
    assert ScopeSettings.model_validate({"enabled": True}).visibility == "group"


@pytest.mark.parametrize(
    "change", ["source_disabled", "receiver_disabled", "source_direct", "receiver_direct", "target_removed"]
)
async def test_ineligible_groups_cannot_share(bot_memory, target_service, connectivity_sessions, change):
    lab = bot_memory
    source = await group(lab, target_service, connectivity_sessions, "product")
    receiver = await group(lab, target_service, connectivity_sessions, "support")
    source = await visibility(lab, source, "installation")
    doc = await create(
        lab.service,
        actor(),
        ACCOUNT_ID,
        source.id,
        CreateDocument(kind="semantic", text="Decision", title="Decision"),
        "seed",
    )
    assert doc.id in (await lab.service.index(actor(), ACCOUNT_ID, receiver.id)).text
    async with transaction(connectivity_sessions) as session:
        row = await session.get(ScopeRecord, source.id if change.startswith("source") else receiver.id)
        if change.endswith("disabled"):
            row.settings_json = {**row.settings_json, "enabled": False}
        elif change.endswith("direct"):
            row.audience = "direct"
        else:
            target = await session.scalar(
                select(AccountTargetRecord).where(
                    AccountTargetRecord.account_id == ACCOUNT_ID, AccountTargetRecord.external_target_id == "support"
                )
            )
            await session.delete(target)
    assert not (await lab.service.index(actor(), ACCOUNT_ID, receiver.id)).entries
    with pytest.raises(ApplicationError):
        await lab.service.get(actor(), ACCOUNT_ID, receiver.id, doc.id)


async def test_visibility_change_is_versioned_and_audited(bot_memory, target_service, connectivity_sessions):
    from a13n_service.iam.models import SecurityAuditRecord

    lab = bot_memory
    scope = await group(lab, target_service, connectivity_sessions, "product")
    updated = await visibility(lab, scope, "installation")
    assert updated.version == scope.version + 1
    with pytest.raises(ApplicationError) as error:
        await visibility(lab, scope, "group")
    assert error.value.code == "version_conflict"
    async with transaction(connectivity_sessions) as session:
        audits = list(
            await session.scalars(select(SecurityAuditRecord).where(SecurityAuditRecord.resource_id == scope.id))
        )
        assert any(a.details.get("visibility") == "installation" for a in audits)


async def test_removed_sharing_commands_are_not_exposed():
    from a13n_service.bots.memory.router import router

    assert not any("publications" in route.path or "sharing-policies" in route.path for route in router.routes)


@pytest.mark.parametrize("boundary", ["account_id", "provider_id", "organization_id", "workspace_id"])
async def test_visibility_never_crosses_installation_or_storage_boundaries(
    bot_memory, target_service, connectivity_sessions, boundary
):
    from a13n_service.bots.memory.models import DocumentRecord
    from a13n_service.bots.memory.queries import visible_documents

    lab = bot_memory
    source = await group(lab, target_service, connectivity_sessions, "product")
    receiver = await group(lab, target_service, connectivity_sessions, "support")
    await visibility(lab, source, "installation")
    doc = await create(
        lab.service,
        actor(),
        ACCOUNT_ID,
        source.id,
        CreateDocument(kind="semantic", text="Decision", title="Decision"),
        "seed",
    )
    async with transaction(connectivity_sessions) as session:
        row = await session.get(ScopeRecord, receiver.id)
        # Project the receiving context under a different trusted boundary. No mutation
        # is persisted; the query must exclude the same active source in every case.
        values = {column.key: getattr(row, column.key) for column in ScopeRecord.__table__.columns}
        values[boundary] = "unrelated_boundary"
        isolated = ScopeRecord(**values)
        assert not list(
            await session.scalars(
                select(DocumentRecord.id).where(DocumentRecord.id == doc.id, visible_documents(isolated))
            )
        )


async def test_legacy_publication_is_never_exposed_and_source_delete_cleans_it_up(
    bot_memory, target_service, connectivity_sessions
):
    from a13n_service.bots.memory.models import (
        DocumentRecord,
        PublicationRecipientRecord,
        SharingParticipantRecord,
        SharingPolicyRecord,
    )

    lab = bot_memory
    source = await group(lab, target_service, connectivity_sessions, "product")
    receiver = await group(lab, target_service, connectivity_sessions, "support")
    doc = await create(
        lab.service,
        actor(),
        ACCOUNT_ID,
        source.id,
        CreateDocument(kind="semantic", text="Original", title="Original"),
        "original",
    )
    legacy = await create(
        lab.service,
        actor(),
        ACCOUNT_ID,
        source.id,
        CreateDocument(kind="semantic", text="Legacy copy", title="Legacy"),
        "legacy",
    )
    async with transaction(connectivity_sessions) as session:
        copy = await session.get(DocumentRecord, legacy.id)
        copy.publication_source_id = doc.id
        session.add(PublicationRecipientRecord(document_id=legacy.id, scope_id=receiver.id))
        session.add(
            SharingPolicyRecord(
                id="mpol_legacy",
                account_id=ACCOUNT_ID,
                provider_id=source.provider_id,
                name="Legacy",
                kinds_json=["long_term"],
                include_history=True,
                enabled=True,
                created_at=utc_now(),
                future_since=utc_now(),
            )
        )
        await session.flush()
        for scope in (source, receiver):
            session.add(SharingParticipantRecord(policy_id="mpol_legacy", scope_id=scope.id, joined_at=utc_now()))
    assert not (await lab.service.index(actor(), ACCOUNT_ID, receiver.id)).entries
    await visibility(lab, source, "installation")
    assert {x.id for x in (await lab.service.index(actor(), ACCOUNT_ID, receiver.id)).entries} == {doc.id}
    assert {x.id for x in (await lab.service.index(actor(), ACCOUNT_ID, source.id)).entries} == {doc.id}
    await delete(lab.service, actor(), ACCOUNT_ID, source.id, doc.id)
    assert not lab.records
    assert not (await lab.service.index(actor(), ACCOUNT_ID, receiver.id)).entries
