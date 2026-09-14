"""Real HTTP Skill publication, lifecycle, concurrency and Run override journeys."""

import asyncio
import hashlib
import json
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import pytest

from ..infrastructure.management_packages import publish_skill, skill_zip, upload
from ..infrastructure.management_support import ManagementJourney

pytestmark = pytest.mark.anyio


async def test_skill_publication_lifecycle_over_http(management: ManagementJourney):
    journey, http = management, management.live.http
    key = "live-skill-" + uuid4().hex
    content = skill_zip(key, "DOCUMENT_ONE", "ATTACHMENT_ONE")
    headers = {"Idempotency-Key": uuid4().hex, "Content-Type": "application/zip"}
    stage_path = journey.base + "/skill-uploads"
    staged = await http.post(stage_path, headers=headers, content=content)
    assert staged.status_code == 201
    receipt = staged.json()
    assert receipt["archive_sha256"] == hashlib.sha256(content).hexdigest()
    replay = await http.post(stage_path, headers=headers, content=content)
    assert replay.status_code == 201 and replay.json() == receipt
    conflict = await http.post(stage_path, headers=headers, content=skill_zip(key, "CHANGED", "ATTACHMENT_ONE"))
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_conflict"

    create_key = uuid4().hex
    body = {"source": {"kind": "zip_upload", "upload_id": receipt["upload_id"]}}
    first = await journey.post(journey.base + "/skills", body, key=create_key)
    assert (await journey.post(journey.base + "/skills", body, key=create_key)) == first
    path = "/api/v1/skills/" + first["skill"]["id"]
    upload_path = "/api/v1/skill-uploads/" + receipt["upload_id"]
    assert (await http.get(upload_path)).json()["consumed_by_revision_id"] == first["revision"]["id"]
    consumed = await http.delete(upload_path)
    assert consumed.status_code == 409 and consumed.json()["error"]["code"] == "skill_upload_consumed"

    duplicate = await upload(journey, "skill-uploads", content, content_type="application/zip")
    conflict = await journey.post(
        journey.base + "/skills",
        {"source": {"kind": "zip_upload", "upload_id": duplicate["upload_id"]}},
        expected=409,
    )
    assert conflict["error"]["code"] == "skill_key_conflict"
    same = await journey.post(
        path + "/revisions",
        {"expected_version": 1, "source": {"kind": "zip_upload", "upload_id": duplicate["upload_id"]}},
        expected=200,
    )
    assert same["outcome"] == "already_current" and same["revision"] == first["revision"]

    second = await publish_skill(journey, key, "DOCUMENT_TWO", "ATTACHMENT_TWO", previous=first["skill"])
    restored = await publish_skill(journey, key, "DOCUMENT_ONE", "ATTACHMENT_ONE", previous=second["skill"])
    assert restored["revision"]["version"] == 3
    assert restored["revision"]["id"] != first["revision"]["id"]
    assert restored["revision"]["manifest"] == first["revision"]["manifest"]
    revisions = await journey.live.request("GET", path + "/revisions", params={"limit": 1})
    versions = [revisions["items"][0]["version"]]
    while revisions["next_cursor"]:
        revisions = await journey.live.request(
            "GET", path + "/revisions", params={"limit": 1, "cursor": revisions["next_cursor"]}
        )
        versions.extend(item["version"] for item in revisions["items"])
        assert len(versions) <= 3
    assert versions == [3, 2, 1]
    for publication, marker in ((first, "ONE"), (second, "TWO"), (restored, "ONE")):
        revision = publication["revision"]
        downloaded = await http.get("/api/v1/skill-revisions/" + revision["id"] + "/content")
        assert downloaded.status_code == 200 and downloaded.headers["content-type"] == "application/zip"
        assert downloaded.headers["etag"] == f'W/"sha256:{revision["manifest"]["content_digest"]}"'
        with ZipFile(BytesIO(downloaded.content)) as archive:
            assert set(archive.namelist()) == {"SKILL.md", "references/proof.txt"}
            assert "DOCUMENT_" + marker in archive.read("SKILL.md").decode()
            assert archive.read("references/proof.txt").decode() == "ATTACHMENT_" + marker

    head = await http.get(path)
    renamed = await http.patch(path, headers={"If-Match": head.headers["etag"]}, json={"name": "Renamed " + key})
    assert renamed.status_code == 200
    assert renamed.json()["key"] == key and renamed.json()["version"] == 3
    stale = await http.patch(path, headers={"If-Match": head.headers["etag"]}, json={"name": "Stale"})
    assert stale.status_code == 412
    keyed = await http.get(journey.base + "/skills/" + key)
    assert keyed.json() == renamed.json() and keyed.headers["etag"] == renamed.headers["etag"]
    searched = await journey.live.request(
        "GET", journey.base + "/skills", params={"q": key.upper(), "source_kind": "zip"}
    )
    assert [item["id"] for item in searched["items"]] == [first["skill"]["id"]]
    deleted = await http.delete(path, headers={"If-Match": renamed.headers["etag"]})
    assert deleted.status_code == 204
    for hidden in (
        path,
        journey.base + "/skills/" + key,
        "/api/v1/skill-revisions/" + first["revision"]["id"] + "/content",
    ):
        assert (await http.get(hidden)).status_code == 404
    assert (await journey.post(journey.base + "/skills", body, key=create_key)) == first
    replacement = await publish_skill(journey, key, "NEW_IDENTITY", "NEW_ATTACHMENT")
    assert replacement["skill"]["id"] != first["skill"]["id"] and replacement["skill"]["version"] == 1


async def test_skill_concurrent_revision_publication_has_one_winner(management: ManagementJourney):
    journey = management
    key = "live-skill-" + uuid4().hex
    first = await publish_skill(journey, key, "ORIGINAL", "ORIGINAL")
    path = "/api/v1/skills/" + first["skill"]["id"]
    candidates = [
        await upload(journey, "skill-uploads", skill_zip(key, marker, marker), content_type="application/zip")
        for marker in ("CANDIDATE_A", "CANDIDATE_B")
    ]
    responses = await asyncio.gather(
        *(
            journey.live.http.post(
                path + "/revisions",
                headers={"Idempotency-Key": uuid4().hex},
                json={"expected_version": 1, "source": {"kind": "zip_upload", "upload_id": candidate["upload_id"]}},
            )
            for candidate in candidates
        )
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    winner = next(response.json() for response in responses if response.status_code == 201)
    loser = next(response.json() for response in responses if response.status_code == 409)
    assert loser["error"]["code"] == "skill_version_conflict"
    assert (await journey.live.request("GET", path))["current_revision_id"] == winner["revision"]["id"]
    revisions = await journey.live.collection(path + "/revisions")
    assert [revision["version"] for revision in revisions] == [2, 1]
    mismatch = await upload(
        journey, "skill-uploads", skill_zip(key + "-other", "BAD", "BAD"), content_type="application/zip"
    )
    rejected = await journey.post(
        path + "/revisions",
        {"expected_version": 2, "source": {"kind": "zip_upload", "upload_id": mismatch["upload_id"]}},
        expected=409,
    )
    assert rejected["error"]["code"] == "skill_key_mismatch"
    assert (await journey.live.request("GET", path))["version"] == 2


async def test_skill_deletion_preserves_accepted_run_but_never_retargets_binding(management: ManagementJourney):
    journey, live = management, management.live
    key = "live-skill-" + uuid4().hex
    first = await publish_skill(journey, key, "RETAINED_DOCUMENT", "RETAINED_ATTACHMENT")
    agent = await journey.agent(skills=[{"skill_key": key}])
    skill_path = "/api/v1/skills/" + first["skill"]["id"]
    references = await live.collection(skill_path + "/references")
    assert [reference["agent_id"] for reference in references] == [agent["agent"]["id"]]
    head = await live.http.get(skill_path)
    blocked = await live.http.delete(skill_path, headers={"If-Match": head.headers["etag"]})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "skill_in_use"

    environment, _ = await journey.environment()
    await journey.lab.stop(journey.lab.workers[0])
    case = await journey.case(steps=[{"tool": "view", "skill_key": key, "skill_file": "references/proof.txt"}])
    accepted = await journey.start(
        case, agent_id=agent["agent"]["id"], environment={"environment_id": environment["id"]}
    )
    await journey.revision(agent["agent"], skills=[])
    assert await live.collection(skill_path + "/references") == []
    deleted = await live.http.delete(skill_path, headers={"If-Match": head.headers["etag"]})
    assert deleted.status_code == 204
    replacement = await publish_skill(journey, key, "REPLACEMENT_DOCUMENT", "REPLACEMENT_ATTACHMENT")
    assert replacement["skill"]["id"] != first["skill"]["id"]
    await journey.lab.start_worker()
    result = await live.finish(accepted["run_id"])
    assert "RETAINED_ATTACHMENT" in result["output_text"] and "REPLACEMENT_ATTACHMENT" not in result["output_text"]
    assert (await live.http.get("/api/v1/skill-revisions/" + first["revision"]["id"] + "/content")).status_code == 404

    for override in (None, {}, {"instructions": "Only instructions change; retain the Skill binding."}):
        rejected_case = await journey.case()
        body = {
            **live.start_body(rejected_case),
            "agent_id": agent["agent"]["id"],
            "agent_revision_id": agent["revision"]["id"],
            "environment": {"environment_id": environment["id"]},
        }
        if override is not None:
            body["config_override"] = override
        rejected = await journey.post(journey.base + "/runs", body, expected=409)
        assert rejected["error"]["code"] == "agent_revision_not_executable"
        assert rejected["error"]["details"]["reason"] == "skill_selection_invalid"
        assert journey.observations(rejected_case) == []
    fresh = await journey.case(steps=[{"tool": "view", "skill_key": key, "skill_file": "references/proof.txt"}])
    receipt = await journey.start(
        fresh,
        agent_id=agent["agent"]["id"],
        agent_revision_id=agent["revision"]["id"],
        config_override={"skills": [{"skill_key": key}]},
        environment={"environment_id": environment["id"]},
    )
    assert "REPLACEMENT_ATTACHMENT" in (await live.finish(receipt["run_id"]))["output_text"]


async def test_skill_run_override_replaces_and_clears_catalog(management: ManagementJourney):
    journey, live = management, management.live
    key = "live-skill-" + uuid4().hex
    first = await publish_skill(journey, key, "VERSION_ONE", "ATTACHMENT_ONE")
    await publish_skill(journey, key, "VERSION_TWO", "ATTACHMENT_TWO", previous=first["skill"])
    agent = await journey.agent(skills=[{"skill_key": key, "version": 1}])
    environment, _ = await journey.environment()
    case = await journey.case(steps=[{"tool": "view", "skill_key": key, "skill_file": "references/proof.txt"}])
    receipt = await journey.start(
        case,
        agent_id=agent["agent"]["id"],
        config_override={"skills": [{"skill_key": key}]},
        environment={"environment_id": environment["id"]},
    )
    assert "ATTACHMENT_TWO" in (await live.finish(receipt["run_id"]))["output_text"]
    empty_case = await journey.case()
    empty = await journey.start(empty_case, agent_id=agent["agent"]["id"], config_override={"skills": []})
    await live.finish(empty["run_id"])
    assert key not in json.dumps(journey.observations(empty_case)[0]["body"]["messages"])
    for selection, status, code, reason in (
        ([{"skill_key": key, "version": 999}], 409, "agent_revision_not_executable", "skill_selection_invalid"),
        ([{"skill_key": key}, {"skill_key": key, "version": 1}], 422, "validation_error", "duplicate_selection"),
    ):
        invalid = await journey.case()
        rejected = await journey.post(
            journey.base + "/runs",
            {
                **live.start_body(invalid),
                "agent_id": agent["agent"]["id"],
                "config_override": {"skills": selection},
                "environment": {"environment_id": environment["id"]},
            },
            expected=status,
        )
        assert rejected["error"]["code"] == code
        assert rejected["error"]["details"]["reason"] == reason
        assert journey.observations(invalid) == []
