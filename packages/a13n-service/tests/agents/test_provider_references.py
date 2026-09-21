"""Provider reference discovery preserves revision history, visibility and cursors."""

from dataclasses import replace

import pytest
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.agents.references import provider_references
from a13n_service.collection_cursors import decode_collection_cursor, encode_collection_cursor
from a13n_service.memory.domain import CreateMemoryProviderRequest
from a13n_service.memory.providers import MemoryProviderService
from a13n_service.memory.resources import MemoryProviderError
from a13n_service.provider_plugins import load_provider_catalogs
from a13n_service.storage import transaction
from a13n_service.web.domain import CreateWebProviderRequest
from a13n_service.web.resources import WebProviderError
from a13n_service.web.service import WebProviderService

from tests.models.conftest import protector
from tests.resource_scope_helpers import organization_admin, sibling_workspace

from .conftest import NOW, ORG_ID, USER_ID, WORKSPACE_ID, actor

pytestmark = pytest.mark.anyio


def reference_config(kind, provider_id, variant=0):
    if kind == "web":
        names = (("search",), ("scrape",), ("search", "scrape"))[variant]
        return {"toolsets": {"web": {"tools": {name: {"config": {"provider_id": provider_id}} for name in names}}}}
    if variant == 0:
        return {"memory": {"provider_id": provider_id}}
    return {"memory": {"entries": [{"backend": {"provider_id": provider_id}}] * variant}}


async def seed_revisions(sessions, key, configs, *, workspace_id=WORKSPACE_ID):
    agent_id = f"agt_{key:016d}"
    revision_ids = [f"agrev_{key:08d}{version:08d}" for version in range(1, len(configs) + 1)]
    async with transaction(sessions) as session:
        session.add(
            AgentRecord(
                id=agent_id,
                organization_id=ORG_ID,
                workspace_id=workspace_id,
                source="custom",
                name=f"Agent {key}",
                key=f"agent-{key}",
                default_revision_id=revision_ids[-1],
                enabled=True,
                created_by_type="user",
                created_by_id=USER_ID,
                updated_by_type="user",
                updated_by_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        for version, (revision_id, config) in enumerate(zip(revision_ids, configs, strict=True), start=1):
            session.add(
                AgentRevisionRecord(
                    id=revision_id,
                    organization_id=ORG_ID,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    version=version,
                    config=config,
                    config_digest="a" * 64,
                    content_digest="b" * 64,
                    resolved_model={},
                    resolved_skills=[],
                    resolved_subagents=[],
                    created_by_type="user",
                    created_by_id=USER_ID,
                    created_at=NOW,
                )
            )
    return agent_id, revision_ids


@pytest.fixture(params=("web", "memory"))
async def provider(request, agent_sessions):
    catalogs = load_provider_catalogs(())
    if request.param == "web":
        service = WebProviderService(agent_sessions, protector(), catalogs.web)
        create = CreateWebProviderRequest(type="exa", name="References", credential={"api_key": "test"})
        error = WebProviderError
    else:
        service = MemoryProviderService(agent_sessions, protector(), catalogs.memory)
        create = CreateMemoryProviderRequest(
            type="mem0_oss",
            name="References",
            configuration={"base_url": "http://unused.invalid/"},
            credential={"api_key": "test"},
        )
        error = MemoryProviderError
    admin = await organization_admin(agent_sessions, actor())
    saved = await service.create(actor=admin, workspace_id=None, request=create)
    return request.param, service, saved.id, error, admin


def cursor_scope(kind, provider_id, selected_actor, workspace_id=WORKSPACE_ID):
    return {
        "resource": f"{kind}_provider:{provider_id}",
        "organization_id": selected_actor.boundary_organization_id,
        "workspace_id": workspace_id,
        "principal": selected_actor.principal.model_dump(mode="json"),
        "type": None,
        "enabled": None,
    }


async def test_provider_pages_include_retained_revisions_without_duplicate_matches(agent_sessions, provider):
    kind, service, provider_id, _, admin = provider
    agent_id, revisions = await seed_revisions(
        agent_sessions, 1, [reference_config(kind, provider_id, variant) for variant in range(3)]
    )
    await seed_revisions(agent_sessions, 2, [reference_config(kind, "other-provider"), {}])
    _, later_revisions = await seed_revisions(agent_sessions, 3, [reference_config(kind, provider_id)])
    sibling = await sibling_workspace(agent_sessions, admin)
    _, sibling_revisions = await seed_revisions(
        agent_sessions, 4, [reference_config(kind, provider_id)], workspace_id=sibling
    )
    first = await service.references(actor=actor(), workspace_id=WORKSPACE_ID, provider_id=provider_id, limit=2)
    assert [item.agent_revision_id for item in first.items] == revisions[:2]
    assert all(item.agent_id == agent_id and not item.is_current for item in first.items)
    assert first.next_cursor is not None
    scope = cursor_scope(kind, provider_id, actor())
    decoded = decode_collection_cursor(first.next_cursor, scope=scope)
    assert (decoded["name"], decoded["id"], decoded["version"]) == (agent_id, revisions[1], 2)
    # An envelope issued before this refactor remains usable, including its legacy field names.
    old_cursor = encode_collection_cursor({"name": agent_id, "id": revisions[1], "version": 2}, scope=scope)
    tail = await service.references(
        actor=actor(), workspace_id=WORKSPACE_ID, provider_id=provider_id, limit=1, cursor=old_cursor
    )
    assert [item.agent_revision_id for item in tail.items] == revisions[2:]
    assert tail.items[0].is_current and tail.next_cursor is not None
    last = await service.references(
        actor=actor(), workspace_id=WORKSPACE_ID, provider_id=provider_id, limit=1, cursor=tail.next_cursor
    )
    assert [item.agent_revision_id for item in last.items] == later_revisions
    assert last.items[0].version == 1 and last.next_cursor is None
    organization_page = await service.references(actor=admin, workspace_id=None, provider_id=provider_id)
    assert [
        item.agent_revision_id for item in organization_page.items
    ] == revisions + later_revisions + sibling_revisions


async def test_visible_page_continues_after_a_full_batch_of_inaccessible_revisions(agent_sessions):
    # A member can read their own Workspace, but has no grant in the sibling Workspace.
    selected_actor = replace(actor(), boundary_workspace_id=None, boundary_organization_id=ORG_ID)
    sibling = await sibling_workspace(agent_sessions, selected_actor)
    config = reference_config("web", "wprov_test1234567890")
    await seed_revisions(agent_sessions, 1, [config] * 101, workspace_id=sibling)
    agent_id, revisions = await seed_revisions(agent_sessions, 2, [config, config])
    async with transaction(agent_sessions) as session:
        items, cursor = await provider_references(
            session,
            actor=selected_actor,
            organization_id=ORG_ID,
            workspace_id=None,
            provider_kind="web",
            provider_id="wprov_test1234567890",
            limit=1,
            position=None,
            scope_key={},
        )
    assert items == ({"agent_id": agent_id, "agent_revision_id": revisions[0], "version": 1, "is_current": False},)
    assert cursor is not None


async def test_provider_cursor_errors_keep_the_public_error_and_validation_order(agent_sessions, provider):
    kind, service, provider_id, error_type, _ = provider
    scope = cursor_scope(kind, provider_id, actor())
    for payload in (
        {},
        {"name": "a", "id": 1, "version": 1},
        {"name": "a", "id": "b", "version": True},
        {"name": "a", "id": "b", "version": 0},
    ):
        cursor = encode_collection_cursor(payload, scope=scope)
        with pytest.raises(error_type) as invalid:
            await service.references(actor=actor(), workspace_id=WORKSPACE_ID, provider_id=provider_id, cursor=cursor)
        assert invalid.value.code == "invalid_cursor"
    for changed_scope in (
        dict(scope, resource="another-provider"),
        dict(scope, workspace_id=None),
        dict(scope, principal={"principal_id": "another-user"}),
    ):
        cursor = encode_collection_cursor({"name": "a", "id": "b", "version": 1}, scope=changed_scope)
        with pytest.raises(error_type) as mismatch:
            await service.references(actor=actor(), workspace_id=WORKSPACE_ID, provider_id=provider_id, cursor=cursor)
        assert mismatch.value.code == "invalid_cursor"
    missing_id = provider_id + "missing"
    # Malformed envelopes are rejected before provider lookup; payload validation comes after it.
    for cursor, expected in (
        ("malformed", "invalid_cursor"),
        (encode_collection_cursor({}, scope=cursor_scope(kind, missing_id, actor())), f"{kind}_provider_not_found"),
    ):
        with pytest.raises(error_type) as missing:
            await service.references(actor=actor(), workspace_id=WORKSPACE_ID, provider_id=missing_id, cursor=cursor)
        assert missing.value.code == expected
