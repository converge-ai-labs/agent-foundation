from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_service.agents.application import AgentManagement
from a13n_service.agents.domain import (
    CreateAgentRequest,
    CreateAgentRevisionRequest,
    DuplicateAgentRequest,
    SetDefaultAgentRevisionRequest,
    UpdateAgentRequest,
)
from a13n_service.agents.errors import AgentError
from a13n_service.agents.models import AgentRecord
from a13n_service.agents.persistence import load_replay, request_identity
from a13n_service.digests import digest_request
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.environments.domain import CreateProviderRequest, CreateTemplateRequest, UpdateTemplateRequest
from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.environments.service import EnvironmentService
from a13n_service.etags import resource_etag
from a13n_service.http_errors import application_error_status
from a13n_service.iam import AuthorizationError
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.storage import transaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, WORKSPACE_ID, actor, agent_config


@pytest.mark.anyio
async def test_create_is_atomic_idempotent_and_starts_at_v1(agent_management: AgentManagement) -> None:
    request = CreateAgentRequest(name="Support", config=agent_config())

    created = await agent_management.commands.create(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="create-support", request=request
    )
    replay = await agent_management.commands.create(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="create-support", request=request
    )

    assert replay == created
    assert created.revision.version == 1
    assert created.agent.default_revision_id == created.revision.id
    assert created.revision.config == request.config
    assert created.revision.config_digest == digest_request(request.config)
    assert created.revision.connection_tools == ()
    assert created.revision.connection_tools == ()


@pytest.mark.anyio
async def test_concurrent_create_with_explicit_key_returns_one_agent(
    agent_management: AgentManagement,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async def create():
        return await agent_management.commands.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="concurrent-create",
            request=CreateAgentRequest(name="Concurrent", key="concurrent", config=agent_config()),
        )

    first, second = await asyncio.gather(create(), create())
    assert first == second
    async with transaction(agent_sessions) as session:
        assert len((await session.scalars(select(AgentRecord))).all()) == 1
        assert await session.scalar(select(IdempotencyEvidenceRecord)) is None


@pytest.mark.anyio
async def test_agent_key_is_retained_with_the_agent(
    agent_management: AgentManagement,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    first = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="expired-agent-key",
        request=CreateAgentRequest(name="First", config=agent_config()),
    )
    async with transaction(agent_sessions) as session:
        row = await session.get(AgentRecord, first.agent.id)
        row.created_at = NOW - timedelta(days=2)
        assert row.request_key is not None
        assert await session.scalar(select(IdempotencyEvidenceRecord)) is None

    second = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="expired-agent-key",
        request=CreateAgentRequest(name="Second", config=agent_config(instructions="Second")),
    )

    assert second.agent.id == first.agent.id


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("config_field", "selection", "reason"),
    [
        (
            "connection_tools",
            ({"connection_id": "cconn_1234567890abcdef", "permission": "allow"},),
            "connection_unavailable",
        ),
        (
            "connection_tools",
            ({"connection_id": "mcpc_1234567890abcdef", "permission": "allow"},),
            "connection_unavailable",
        ),
    ],
)
async def test_agent_creation_rejects_unavailable_connections(
    agent_management: AgentManagement,
    config_field: str,
    selection: dict[str, object],
    reason: str,
) -> None:
    config = agent_config(**{config_field: selection})

    with pytest.raises(AgentError) as rejected:
        await agent_management.commands.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key=f"create-{config_field}",
            request=CreateAgentRequest(name=f"Agent {config_field}", config=config),
        )

    assert rejected.value.code == "agent_revision_create_failed"
    assert rejected.value.details == {"reason": reason, "path": f"{config_field}.0"}


@pytest.mark.anyio
async def test_revision_noop_default_switch_and_monotonic_lineage(
    agent_management: AgentManagement,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-lineage",
        request=CreateAgentRequest(name="Lineage", config=agent_config()),
    )

    noop = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="noop-lineage",
        request=CreateAgentRevisionRequest(config=agent_config()),
        if_match=resource_etag(created.agent.id, created.agent.updated_at),
    )
    note_only = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="note-only-lineage",
        request=CreateAgentRevisionRequest(config=agent_config(), change_summary="No configuration change"),
        if_match=resource_etag(noop.agent.id, noop.agent.updated_at),
    )
    second = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="second-lineage",
        request=CreateAgentRevisionRequest(
            config=agent_config(instructions="Analyze carefully."),
            change_summary="Explain the new instructions",
        ),
        if_match=resource_etag(noop.agent.id, noop.agent.updated_at),
    )
    assert noop.revision.id == created.revision.id
    assert noop.agent.default_revision_id == created.revision.id
    assert note_only.revision.id == created.revision.id
    assert note_only.agent.default_revision_id == created.revision.id
    assert second.revision.version == 2
    assert second.revision.change_summary == "Explain the new instructions"
    summary_only = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="summary-only-after-v2",
        request=CreateAgentRevisionRequest(
            config=agent_config(instructions="Analyze carefully."), change_summary="Different note"
        ),
        if_match=resource_etag(second.agent.id, second.agent.updated_at),
    )
    assert summary_only.revision == second.revision
    selected = await agent_management.revisions.set_default_revision(
        actor=actor(),
        agent_id=created.agent.id,
        revision_id=created.revision.id,
        idempotency_key="select-lineage",
        request=SetDefaultAgentRevisionRequest(),
        if_match=resource_etag(second.agent.id, second.agent.updated_at),
    )
    assert selected.agent.updated_at != second.agent.updated_at
    assert selected.revision.id == created.revision.id
    assert selected.agent.default_revision_id == created.revision.id
    replay = await agent_management.revisions.set_default_revision(
        actor=actor(),
        agent_id=created.agent.id,
        revision_id=created.revision.id,
        idempotency_key="select-lineage",
        request=SetDefaultAgentRevisionRequest(),
        if_match=resource_etag(second.agent.id, second.agent.updated_at),
    )
    assert replay == selected
    changed = await agent_management.revisions.set_default_revision(
        actor=actor(),
        agent_id=created.agent.id,
        revision_id=second.revision.id,
        idempotency_key="select-lineage",
        request=SetDefaultAgentRevisionRequest(),
        if_match=resource_etag(selected.agent.id, selected.agent.updated_at),
    )
    assert changed == replay
    same_default = await agent_management.revisions.set_default_revision(
        actor=actor(),
        agent_id=created.agent.id,
        revision_id=created.revision.id,
        idempotency_key="same-default-lineage",
        request=SetDefaultAgentRevisionRequest(),
        if_match=resource_etag(selected.agent.id, selected.agent.updated_at),
    )
    assert same_default.agent.updated_at == selected.agent.updated_at
    revisions = await agent_management.queries.list_revisions(
        actor=actor(), agent_id=created.agent.id, limit=10, cursor=None
    )
    assert [item.version for item in revisions.items] == [2, 1]
    async with transaction(agent_sessions) as session:
        audits = tuple(
            await session.scalars(
                select(SecurityAuditRecord).where(
                    SecurityAuditRecord.resource_id == created.agent.id,
                    SecurityAuditRecord.action.in_(("agent.revision.create", "agent.revision.set_default")),
                )
            )
        )
    assert {(audit.action, audit.details["from_revision_id"], audit.details["to_revision_id"]) for audit in audits} == {
        ("agent.revision.create", created.revision.id, second.revision.id),
        ("agent.revision.set_default", second.revision.id, created.revision.id),
    }


@pytest.mark.anyio
async def test_concurrent_revision_and_selection_replay_one_result(
    agent_management: AgentManagement, agent_sessions: async_sessionmaker[AsyncSession]
) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="concurrent-agent",
        request=CreateAgentRequest(name="Concurrent", config=agent_config()),
    )
    request = CreateAgentRevisionRequest(config=agent_config(instructions="Concurrent update"))
    etag = resource_etag(created.agent.id, created.agent.updated_at)

    async def save():
        return await agent_management.revisions.create_revision(
            actor=actor(),
            agent_id=created.agent.id,
            idempotency_key="concurrent-save",
            request=request,
            if_match=etag,
        )

    first, replay = await asyncio.gather(save(), save())
    assert first == replay and first.revision.version == 2
    selected_etag = resource_etag(first.agent.id, first.agent.updated_at)

    async def select_default():
        return await agent_management.revisions.set_default_revision(
            actor=actor(),
            agent_id=created.agent.id,
            revision_id=created.revision.id,
            idempotency_key="concurrent-select",
            request=SetDefaultAgentRevisionRequest(),
            if_match=selected_etag,
        )

    switched, selected_replay = await asyncio.gather(select_default(), select_default())
    assert switched == selected_replay
    history = await agent_management.queries.list_revisions(
        actor=actor(), agent_id=created.agent.id, limit=10, cursor=None
    )
    assert [revision.version for revision in history.items] == [2, 1]
    async with transaction(agent_sessions) as session:
        actions = tuple(
            await session.scalars(
                select(SecurityAuditRecord.action).where(
                    SecurityAuditRecord.resource_id == created.agent.id,
                    SecurityAuditRecord.action.in_(("agent.revision.create", "agent.revision.set_default")),
                )
            )
        )
    assert sorted(actions) == ["agent.revision.create", "agent.revision.set_default"]


@pytest.mark.anyio
async def test_revision_create_rejects_stale_head_etag(agent_management: AgentManagement) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-conflict",
        request=CreateAgentRequest(name="Conflict", config=agent_config()),
    )
    await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="advance-conflict",
        request=CreateAgentRevisionRequest(
            config=agent_config(instructions="Changed"),
        ),
        if_match=resource_etag(created.agent.id, created.agent.updated_at),
    )

    with pytest.raises(AgentError) as stale:
        await agent_management.revisions.create_revision(
            actor=actor(),
            agent_id=created.agent.id,
            idempotency_key="stale-conflict",
            request=CreateAgentRevisionRequest(
                config=agent_config(instructions="Stale"),
            ),
            if_match=resource_etag(created.agent.id, created.agent.updated_at),
        )
    assert application_error_status(stale.value) == 412
    assert "current_etag" in stale.value.details


@pytest.mark.anyio
async def test_unavailable_historical_environment_blocks_default_switch_atomically(
    agent_management: AgentManagement, agent_sessions: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    environments = EnvironmentService(
        agent_sessions,
        ProviderCatalog(select_builtin_environment_providers(("direct_local",))),
        SecretProtector.from_base64(encoded_key=base64.b64encode(b"e" * 32).decode(), encryption_key_id="test"),
    )
    provider = await environments.create_provider(
        actor=actor(), workspace_id=WORKSPACE_ID, request=CreateProviderRequest(type="direct_local", name="Local")
    )
    template = await environments.create_template(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="historical-template",
        request=CreateTemplateRequest(
            name="Historical",
            provider_id=provider.id,
            configuration={"root": {"path": str(tmp_path)}},
            preparation="on_use",
            retention={"idle": {"stop_after": None, "delete_after": None}},
        ),
    )
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="historical-environment",
        request=CreateAgentRequest(
            name="Historical environment",
            config=agent_config().model_copy(update={"default_environment_template_id": template.id}),
        ),
    )
    second = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="historical-environment-v2",
        request=CreateAgentRevisionRequest(config=agent_config(instructions="v2")),
        if_match=resource_etag(created.agent.id, created.agent.updated_at),
    )
    copy = await agent_management.duplication.duplicate(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="copy-current-environment",
        request=DuplicateAgentRequest(name="Template default copy"),
        if_match=resource_etag(second.agent.id, second.agent.updated_at),
    )
    copied_revision = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=copy.id,
        idempotency_key="copy-environment-choice",
        request=CreateAgentRevisionRequest(config=created.revision.config),
        if_match=resource_etag(copy.id, copy.updated_at),
    )
    await environments.update_template(
        actor=actor(),
        template_id=template.id,
        request=UpdateTemplateRequest(archived=True),
        if_match=resource_etag(template.id, template.updated_at),
    )
    with pytest.raises(EnvironmentManagementError) as unavailable:
        await agent_management.revisions.set_default_revision(
            actor=actor(),
            agent_id=created.agent.id,
            revision_id=created.revision.id,
            idempotency_key="historical-environment-switch",
            request=SetDefaultAgentRevisionRequest(),
            if_match=resource_etag(second.agent.id, second.agent.updated_at),
        )
    assert unavailable.value.code == "environment_not_found"
    with pytest.raises(EnvironmentManagementError):
        await agent_management.duplication.duplicate(
            actor=actor(),
            agent_id=copy.id,
            idempotency_key="duplicate-unavailable-template",
            request=DuplicateAgentRequest(name="Unavailable copy"),
            if_match=resource_etag(copy.id, copied_revision.agent.updated_at),
        )
    async with transaction(agent_sessions) as session:
        head = await session.get(AgentRecord, created.agent.id)
        assert head.default_revision_id == second.revision.id


@pytest.mark.anyio
async def test_metadata_and_lifecycle_each_advance_etag(
    agent_management: AgentManagement,
) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-metadata",
        request=CreateAgentRequest(name="Metadata", config=agent_config()),
    )
    etag = resource_etag(created.agent.id, created.agent.updated_at)

    updated = await agent_management.commands.patch_metadata(
        actor=actor(),
        agent_id=created.agent.id,
        if_match=etag,
        request=UpdateAgentRequest(name="Renamed"),
    )
    with pytest.raises(AgentError) as stale:
        await agent_management.commands.patch_metadata(
            actor=actor(),
            agent_id=created.agent.id,
            if_match='"stale"',
            request=UpdateAgentRequest(name="Rejected"),
        )
    assert application_error_status(stale.value) == 412
    disabled = await agent_management.commands.change_lifecycle(
        actor=actor(),
        agent_id=created.agent.id,
        action="disable",
        idempotency_key="disable-metadata",
        if_match=resource_etag(updated.id, updated.updated_at),
    )
    archived = await agent_management.commands.change_lifecycle(
        actor=actor(),
        agent_id=created.agent.id,
        action="archive",
        idempotency_key="archive-metadata",
        if_match=resource_etag(disabled.id, disabled.updated_at),
    )

    assert updated.name == "Renamed"
    assert len({updated.updated_at, disabled.updated_at, archived.updated_at}) == 3
    assert not disabled.enabled
    assert archived.archived_at is not None


@pytest.mark.anyio
async def test_duplicate_copies_exact_current_revision_as_new_v1(agent_management: AgentManagement) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-duplicate",
        request=CreateAgentRequest(name="Original", config=agent_config()),
    )
    second = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="advance-duplicate",
        request=CreateAgentRevisionRequest(
            config=agent_config(instructions="Current"),
        ),
        if_match=resource_etag(created.agent.id, created.agent.updated_at),
    )

    duplicate = await agent_management.duplication.duplicate(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="duplicate",
        request=DuplicateAgentRequest(name="Copy"),
        if_match=resource_etag(second.agent.id, second.agent.updated_at),
    )
    duplicate_revision = await agent_management.queries.get_revision(
        actor=actor(), revision_id=duplicate.default_revision_id
    )

    assert duplicate_revision.version == 1
    assert duplicate.duplicated_from_revision_id == second.revision.id
    assert duplicate_revision.source_revision_id == second.revision.id
    assert duplicate_revision.config.instructions == "Current"


@pytest.mark.anyio
async def test_list_filters_enabled_and_archived_axes(agent_management: AgentManagement) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-list",
        request=CreateAgentRequest(name="Listed", config=agent_config()),
    )
    disabled = await agent_management.commands.change_lifecycle(
        actor=actor(),
        agent_id=created.agent.id,
        action="disable",
        idempotency_key="disable-list",
        if_match=resource_etag(created.agent.id, created.agent.updated_at),
    )

    page = await agent_management.queries.list(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        limit=10,
        cursor=None,
        enabled=False,
        source=None,
        include_archived=False,
    )
    assert page.items == (disabled,)


@pytest.mark.anyio
async def test_create_replay_ignores_changed_agent_content(agent_management: AgentManagement) -> None:
    original = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="conflicting-create",
        request=CreateAgentRequest(name="Original", config=agent_config()),
    )

    changed = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="conflicting-create",
        request=CreateAgentRequest(name="Different", config=agent_config()),
    )
    assert changed == original


@pytest.mark.anyio
async def test_agent_replay_rejects_organization_boundary(agent_sessions: async_sessionmaker[AsyncSession]) -> None:
    organization_actor = replace(actor(), boundary_workspace_id=None, boundary_organization_id="org_test")
    async with transaction(agent_sessions) as session:
        with pytest.raises(AuthorizationError, match="workspace_boundary_required"):
            await load_replay(
                session,
                actor=organization_actor,
                operation="agent.create",
                scope_id=WORKSPACE_ID,
                identity=request_identity("workspace-only"),
                now=NOW,
            )
