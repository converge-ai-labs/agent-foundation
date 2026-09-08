"""Case 24: ZIP publication, frozen Skill versions and real document/attachment reads."""

import json
from uuid import uuid4

import pytest

from .management_packages import publish_skill

pytestmark = pytest.mark.anyio


async def test_skill_version_materialization_and_workspace_isolation(management):
    journey, live = management, management.live
    key = "live-skill-" + uuid4().hex
    first = await publish_skill(journey, key, "DOCUMENT_ONE", "ATTACHMENT_ONE")
    environment, root = await journey.environment()
    selection = {"environment_id": environment["id"]}
    current = await journey.agent(skills=[{"skill_key": key}])
    pinned = await journey.agent(skills=[{"skill_key": key, "version": 1}])

    async def start(agent_id):
        case = await journey.case(
            steps=[
                {"tool": "view", "skill_key": key, "skill_file": "SKILL.md"},
                {"tool": "view", "skill_key": key, "skill_file": "references/proof.txt"},
            ]
        )
        return case, await journey.start(case, agent_id=agent_id, environment=selection)

    await journey.lab.stop(journey.lab.workers[0])
    frozen_case, frozen = await start(current["agent"]["id"])
    second = await publish_skill(journey, key, "DOCUMENT_TWO", "ATTACHMENT_TWO", previous=first["skill"])
    assert second["skill"]["version"] == 2
    await journey.lab.start_worker()
    for case, receipt, version in (
        (frozen_case, frozen, "ONE"),
        (*(await start(current["agent"]["id"])), "TWO"),
        (*(await start(pinned["agent"]["id"])), "ONE"),
    ):
        result = await live.finish(receipt["run_id"])
        observed = journey.observations(case)
        messages = [message for message in observed[-1]["body"]["messages"] if message.get("role") == "tool"]
        assert len(messages) == 2
        assert "DOCUMENT_" + version in json.dumps(messages[0]["content"])
        assert "ATTACHMENT_" + version in result["output_text"]
    # Files have actually crossed object storage -> Worker -> selected Environment.
    documents = list(root.rglob("SKILL.md"))
    assert any("DOCUMENT_ONE" in path.read_text() for path in documents)
    assert any("DOCUMENT_TWO" in path.read_text() for path in documents)
    async with journey.outsider() as outsider:
        for path in (
            f"/api/v1/skills/{first['skill']['id']}",
            f"/api/v1/skill-revisions/{first['revision']['id']}",
            f"/api/v1/skill-revisions/{first['revision']['id']}/content",
        ):
            response = await outsider.get(path)
            assert response.status_code in {403, 404}
            assert "DOCUMENT_ONE" not in response.text and "ATTACHMENT_ONE" not in response.text
    await journey.post(
        journey.base + "/runs",
        {
            **live.start_body(await journey.case()),
            "agent_id": current["agent"]["id"],
            "environment": None,
        },
        expected=400,
    )
