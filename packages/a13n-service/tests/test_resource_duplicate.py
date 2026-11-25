"""Resource copies retain configuration while creating independent identities."""

import pytest
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.skills.tables import SkillRevisionRow
from a13n_service.runs.schedules import service as schedules
from a13n_service.runs.schedules.schemas import ScheduleDuplicate
from a13n_service.runs.schedules.tables import ScheduleRow
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal
from a13n_service.tenancy.tables import PrincipalRow
from sqlalchemy import select

from .test_models import manual, post, price, provider
from .test_schedules import configuration
from .test_skills import DOCUMENT, archive, create, stage

pytestmark = pytest.mark.anyio


async def audit(service, target: str, kind: str, actor: str, source: str) -> AuditEventRow:
    async with short_session(service.runtime.storage) as session:
        event = (await session.scalars(select(AuditEventRow).where(AuditEventRow.target_id == target))).one()
        assert event.action == f"{kind}.duplicate"
        assert event.actor_id == actor and event.details["source_id"] == source
        return event


async def test_template_copy_revalidates_provider_and_preserves_disabled_state(service):
    from .environments_support import with_fake_providers

    env = await with_fake_providers(service)
    base = f"{env.api}/environment-templates/{env.template['id']}"
    original = await env.client.get(base)
    disabled = await env.client.patch(
        base, headers={"If-Match": original.headers["etag"]}, json={"enabled": False, "labels": {"team": "ops"}}
    )
    assert disabled.status_code == 200, disabled.text
    result = await env.client.post(f"{base}/duplicate", json={})
    assert result.status_code == 201, result.text
    clone = result.json()
    assert result.headers["etag"] == f'"{clone["id"]}:{clone["version"]}"'
    assert clone["name"] == f"{env.template['name']} (copy)"
    for field in ("config", "provider_id", "description", "labels", "enabled"):
        assert clone[field] == disabled.json()[field]
    await audit(service, clone["id"], "environment_template", service.tenant.principal_id, env.template["id"])
    path = f"{env.api}/environment-providers/{env.provider['id']}"
    before = await env.client.get(path)
    patched = await env.client.patch(path, headers={"If-Match": before.headers["etag"]}, json={"enabled": False})
    assert patched.status_code == 200, patched.text
    refusal = await env.client.post(f"{base}/duplicate", json={})
    assert refusal.status_code == 422, refusal.text
    assert refusal.json()["error"]["code"] == "disabled"


async def test_model_copy_checks_key_and_provider(service):
    source_provider = await provider(service)
    source = await post(
        service,
        "/models",
        {
            **manual(source_provider["id"], "original-model"),
            "enabled": False,
            "pricing": price("original-model"),
            "catalog_ref": {"provider": "openai", "model": "original-model"},
        },
    )
    base = "/models/original-model/duplicate"
    await post(service, base, {"key": "original-model"}, 409)
    clone = await post(service, base, {"key": "new-model", "description": "A new description"})
    assert clone["key"] == "new-model" and clone["name"] == "original-model (copy)"
    assert clone["description"] == "A new description"
    for field in ("provider_id", "config", "pricing", "catalog_ref", "enabled"):
        assert clone[field] == source[field]
    async with short_session(service.runtime.storage) as session:
        events = (await session.scalars(select(AuditEventRow).where(AuditEventRow.action == "model.duplicate"))).all()
        (event,) = events
        assert event.details["key"] == "new-model" and event.actor_id == service.tenant.principal_id
    path = f"{service.api}/model-providers/{source_provider['id']}"
    before = await service.client.get(path)
    patched = await service.client.patch(path, headers={"If-Match": before.headers["etag"]}, json={"enabled": False})
    assert patched.status_code == 200, patched.text
    await post(service, base, {"key": "refused-model"}, 422)


async def test_schedule_copy_has_fresh_authority_and_empty_history(service, scripted_model, runs_kit):
    agent = await runs_kit.create_agent(service, scripted_model)
    body = configuration(agent["id"], state="enabled")
    original = await service.client.post(f"{service.api}/schedules", json=body, headers={"Idempotency-Key": "source"})
    assert original.status_code == 201, original.text
    source = original.json()
    tenant = service.tenant
    other_id = new_object_id("usr")
    async with transaction(service.runtime.storage) as session:
        session.add(PrincipalRow(id=other_id, kind="user", name="Other", email="other@example.com"))
    for verbs in (frozenset({"write", "run"}), frozenset({"read", "write"})):
        actor = Principal(other_id, "user", (Grant(tenant.organization_id, tenant.workspace_id, verbs),))
        with pytest.raises(ServiceError) as denied:
            await schedules.duplicate(service.runtime, actor, tenant.workspace_id, source["id"], ScheduleDuplicate())
        assert denied.value.code == "forbidden"
    other = Principal(
        other_id, "user", (Grant(tenant.organization_id, tenant.workspace_id, BUILT_IN_ROLES["builder"]),)
    )
    with pytest.raises(ServiceError) as missing:
        await schedules.duplicate(
            service.runtime,
            other,
            tenant.workspace_id,
            source["id"],
            ScheduleDuplicate(target={"session_id": new_object_id("ses")}),
        )
    assert missing.value.code == "not_found"
    clone = await schedules.duplicate(service.runtime, other, tenant.workspace_id, source["id"], ScheduleDuplicate())
    assert clone.principal_id == clone.created_by_id == other_id
    assert clone.state == "paused" and clone.pause_reason == "manual"
    assert clone.session_id == source["session_id"] and clone.id != source["id"]
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, clone.id)
        assert row.authority["principal_id"] == other_id
        assert set(row.authority["verbs"]) == BUILT_IN_ROLES["builder"]
        assert (
            row.last_occurrence_id
            is row.pending_occurrence_id
            is row.next_fire_at
            is row.last_processed_fire_at
            is None
        )
    history = await service.client.get(f"{service.api}/schedules/{clone.id}/occurrences")
    assert history.status_code == 200 and history.json()["items"] == []
    await audit(service, clone.id, "schedule", other_id, source["id"])


async def test_skill_copy_shares_selected_package_but_owns_its_revisions(service):
    first = archive({"SKILL.md": DOCUMENT})
    skill = await create(service, first, "original", labels={"team": "ops"})
    path = f"{service.api}/skills/{skill['id']}"
    second = await stage(service, archive({"SKILL.md": DOCUMENT + b"\nNew content."}), "second")
    revision = await service.client.post(
        f"{path}/revisions",
        headers={"If-Match": (await service.client.get(path)).headers["etag"]},
        json={"source": {"kind": "upload", "upload_id": second}},
    )
    assert revision.status_code == 201, revision.text
    picked = skill["default_revision_id"]
    response = await service.client.post(f"{path}/duplicate", json={"revision_id": picked})
    assert response.status_code == 201, response.text
    clone = response.json()
    assert clone["labels"] == skill["labels"] and clone["name"] == "code-review (copy)"
    assert clone["default_revision"]["number"] == 1 and clone["default_revision_id"] != picked
    async with short_session(service.runtime.storage) as session:
        before = await session.get_one(SkillRevisionRow, picked)
        after = await session.get_one(SkillRevisionRow, clone["default_revision_id"])
        assert before.package_ref == after.package_ref and before.config == after.config
    event = await audit(service, clone["id"], "skill", service.tenant.principal_id, skill["id"])
    assert event.details["source_revision_id"] == picked
    unrelated = await service.client.post(f"{path}/duplicate", json={"revision_id": clone["default_revision_id"]})
    assert unrelated.status_code == 404, unrelated.text
    assert (await service.client.get(path)).json()["default_revision_id"] == revision.json()["id"]
