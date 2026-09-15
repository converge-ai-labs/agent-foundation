"""HTTP Skill resources and bounded process-shared test barriers."""

import json
from uuid import uuid4

from ..infrastructure.management_packages import publish_skill
from ..infrastructure.management_support import ManagementJourney
from ..infrastructure.run_faults import arm


class SkillJourney(ManagementJourney):
    def arm(self, point, *, role="control", **match):
        return arm(self.lab.root / "faults", uuid4().hex, point=point, role=role, match=match)

    async def reached(self, barrier):
        async def read():
            try:
                return json.loads((barrier / "hit-1.json").read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                return None

        return await self.live.wait(read, bool, "Skill fault barrier")

    @staticmethod
    def release(barrier):
        (barrier / "release").touch()

    def release_all(self):
        for rule in (self.lab.root / "faults").glob("*/rule.json"):
            self.release(rule.parent)

    async def skill(self):
        key = "live-skill-" + uuid4().hex
        return await publish_skill(self, key, "DOCUMENT_ONE", "ATTACHMENT_ONE")

    async def delete_skill(self, skill):
        path = "/api/v1/skills/" + skill["id"]
        head = await self.live.http.get(path)
        assert head.status_code == 200
        response = await self.live.http.delete(path, headers={"If-Match": head.headers["etag"]})
        assert response.status_code == 204, response.status_code

    async def lifecycle(self, agent, action):
        path = self.base + "/agents/" + agent["id"]
        head = await self.live.http.get(path)
        assert head.status_code == 200
        return await self.live.request(
            "POST",
            path + "/" + action,
            headers={"If-Match": head.headers["etag"], "Idempotency-Key": uuid4().hex},
        )


def proof(key):
    return {"tool": "view", "skill_key": key, "skill_file": "references/proof.txt"}
