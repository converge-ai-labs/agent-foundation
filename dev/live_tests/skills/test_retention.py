"""HTTP publications remain usable after real upload and object collection."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ..infrastructure.management_packages import skill_zip, upload
from .support import SkillJourney, proof

pytestmark = pytest.mark.anyio


async def collect(skills, staged, *, now=None):
    output = "retention-" + uuid4().hex + ".json"
    await skills.lab.command(
        "dev.live_tests.skills.retention_probe",
        staged["manifest"]["content_digest"],
        (now or datetime.now(UTC) + timedelta(hours=25)).isoformat(),
        output,
    )
    return json.loads((skills.lab.root / output).read_text())


@pytest.mark.parametrize("owner", ["none", "live-upload", "revision", "deleted-run"])
async def test_skill_retention_preserves_only_owned_packages(skills: SkillJourney, owner):
    key = "retention-" + uuid4().hex
    archive = skill_zip(key, "DOCUMENT_ONE", "ATTACHMENT_ONE")
    staged = await upload(skills, "skill-uploads", archive, content_type="application/zip")
    now = None
    survivor = None
    receipt = None
    if owner == "live-upload":
        survivor = await upload(skills, "skill-uploads", archive, content_type="application/zip")
        start, end = (
            datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00")) for value in (staged, survivor)
        )
        assert start < end
        now = start + (end - start) / 2
    elif owner in {"revision", "deleted-run"}:
        published = await skills.post(
            skills.base + "/skills", {"source": {"kind": "zip_upload", "upload_id": staged["upload_id"]}}
        )
        if owner == "deleted-run":
            await skills.lab.stop(skills.lab.workers[-1])
            agent = await skills.agent(skills=[{"skill_key": key}])
            environment, _ = await skills.environment()
            case = await skills.case(steps=[proof(key)])
            receipt = await skills.start(
                case, agent_id=agent["agent"]["id"], environment={"environment_id": environment["id"]}
            )
            await skills.revision(agent["agent"], skills=[])
            await skills.delete_skill(published["skill"])
    try:
        result = await collect(skills, staged, now=now)
        assert staged["upload_id"] not in result["uploads"]
        assert result["collected"] == (owner == "none"), result
        assert result["present"] == (owner != "none"), result
        assert (await skills.live.http.get("/api/v1/skill-uploads/" + staged["upload_id"])).status_code == 404
        if survivor:
            assert survivor["upload_id"] in result["uploads"]
            published = await skills.post(
                skills.base + "/skills", {"source": {"kind": "zip_upload", "upload_id": survivor["upload_id"]}}
            )
        if owner in {"live-upload", "revision"}:
            content = await skills.live.http.get("/api/v1/skill-revisions/" + published["revision"]["id"] + "/content")
            assert content.status_code == 200
            assert content.headers["etag"] == 'W/"sha256:' + staged["manifest"]["content_digest"] + '"'
    finally:
        if owner == "deleted-run":
            await skills.lab.start_worker()
    if receipt:
        completed = await skills.live.finish(receipt["run_id"])
        assert "ATTACHMENT_ONE" in completed["output_text"]


@pytest.mark.parametrize("winner", ["collector", "publication"])
async def test_skill_collection_fences_concurrent_republication(skills: SkillJourney, winner):
    key = "gc-race-" + uuid4().hex
    archive = skill_zip(key, "DOCUMENT_ONE", "ATTACHMENT_ONE")
    staged = await upload(skills, "skill-uploads", archive, content_type="application/zip")
    removed = await skills.live.http.delete("/api/v1/skill-uploads/" + staged["upload_id"])
    assert removed.status_code == 204
    barrier = skills.arm(
        "skill.collector_claimed" if winner == "collector" else "skill.before_collect",
        digest=staged["manifest"]["content_digest"],
    )
    task = asyncio.create_task(collect(skills, staged, now=datetime.now(UTC)))
    request_key = uuid4().hex

    async def stage():
        return await skills.live.http.post(
            skills.base + "/skill-uploads",
            content=archive,
            headers={"Content-Type": "application/zip", "Idempotency-Key": request_key},
        )

    try:
        await skills.reached(barrier)
        response = await stage()
        if winner == "collector":
            assert response.status_code == 503, response.status_code
            skills.release(barrier)
            result = await task
            assert result["collected"] and not result["present"]
            response = await stage()
        assert response.status_code == 201
        published = await skills.post(
            skills.base + "/skills", {"source": {"kind": "zip_upload", "upload_id": response.json()["upload_id"]}}
        )
        if winner == "publication":
            skills.release(barrier)
            result = await task
            assert not result["collected"] and result["present"]
        agent = await skills.agent(skills=[{"skill_key": key}])
        environment, _ = await skills.environment()
        case = await skills.case(steps=[proof(key)])
        receipt = await skills.start(
            case, agent_id=agent["agent"]["id"], environment={"environment_id": environment["id"]}
        )
        assert "ATTACHMENT_ONE" in (await skills.live.finish(receipt["run_id"]))["output_text"]
        assert published["revision"]["manifest"] == staged["manifest"]
    finally:
        skills.release(barrier)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
