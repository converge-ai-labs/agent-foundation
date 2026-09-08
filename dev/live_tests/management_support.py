"""HTTP resource helpers and private model observations for management journeys."""

import json
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx2

from .round_two_lab import private_json
from .round_two_resources import agent_config


class ManagementJourney:
    def __init__(self, lab):
        self.lab, self.live = lab, lab.client
        self.base = f"/api/v1/workspaces/{self.live.config['workspace_id']}"

    async def post(self, path, body, *, expected=201, key=None):
        return await self.live.request(
            "POST", path, expected=expected, headers={"Idempotency-Key": key or uuid4().hex}, json=body
        )

    async def patch(self, path, body):
        response = await self.live.http.get(path)
        assert response.status_code == 200 and response.headers.get("etag")
        return await self.live.request("PATCH", path, headers={"If-Match": response.headers["etag"]}, json=body)

    async def agent(self, **config):
        return await self.post(
            self.base + "/agents", {"name": "Management " + uuid4().hex, "config": agent_config(**config)}
        )

    async def revision(self, agent, **config):
        return await self.post(
            f"/api/v1/agents/{agent['id']}/revisions",
            {"expected_version": agent["version"], "config": agent_config(**config)},
        )

    async def case(self, **plan):
        case = await self.live.case("management")
        self.plan(case, **plan)
        return case

    def plan(self, case, **plan):
        private_json(self.lab.root / "workspace" / case["case_id"] / "plan.json", plan)

    async def start(self, case, *, agent_id=None, **values):
        body = {**self.live.start_body(case), **values}
        if agent_id:
            body["agent_id"] = agent_id
        receipt = await self.post(self.base + "/runs", body, expected=202)
        self.live.track(receipt)
        return receipt

    def observations(self, case):
        path = self.lab.root / "workspace" / case["case_id"] / "observations.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    async def ready(self, case, run_id):
        return await self.lab.wait_evidence(case, "management_ready", run_id=run_id)

    async def environment_template(self, *, preparation="on_run", access="full", name=None):
        name = name or uuid4().hex
        root = self.lab.root / ("environment-" + name)
        root.mkdir(mode=0o700)
        provider = await self.post(
            self.base + "/environment-providers",
            {
                "type": "a13n.direct-local",
                "name": name,
                "configuration": {},
            },
        )
        recipe = {
            "provider_id": provider["id"],
            "access": access,
            "preparation": preparation,
            "retention": {"idle": {"stop_after": None, "delete_after": None}},
            "configuration": {
                "root": {"path": str(root)},
                "shell_profiles": [{"profile_id": "sh", "executable": "/bin/sh"}],
            },
        }
        template = await self.post(self.base + "/environment-templates", {"name": name, **recipe})
        return template, recipe, root

    async def environment(self, **options):
        template, _, root = await self.environment_template(**options)
        resource = await self.post(self.base + "/environments", {"template_id": template["id"]})
        return resource, root

    async def environment_command(self, environment_id, action):
        command = await self.post(f"/api/v1/environments/{environment_id}/{action}", {}, expected=202)
        completed = await self.live.wait(
            lambda: self.live.request("GET", f"/api/v1/environment-commands/{command['id']}"),
            lambda value: value["status"] != "pending",
            f"Environment {action}",
        )
        assert completed["status"] == "completed", completed
        return await self.live.request("GET", f"/api/v1/environments/{environment_id}")

    @asynccontextmanager
    async def outsider(self):
        other = self.live.config["other_identity"]
        async with httpx2.AsyncClient(
            base_url=self.live.config["control_url"],
            headers={"Authorization": "Bearer " + other["token"]},
            timeout=5,
            trust_env=False,
            follow_redirects=False,
        ) as http:
            probe = await http.get(f"/api/v1/workspaces/{other['workspace_id']}/runs")
            assert probe.status_code == 200
            yield http


def tool_names(observation):
    return {entry["function"]["name"] for entry in observation["body"].get("tools", [])}


def has_tool(observation, name):
    return any(value == name or value.endswith("_" + name) for value in tool_names(observation))


def client_tool():
    return {
        "name": "live_client",
        "description": "Obtain a value from the test client.",
        "parameters_json_schema": {
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"],
        },
    }


def last_tool_result(observation):
    messages = [message for message in observation["body"]["messages"] if message.get("role") == "tool"]
    value = messages[-1]["content"]
    while isinstance(value, str):
        value = json.loads(value)
    return value


async def feedback_body(live, waiting, result):
    pending = await live.request("GET", f"/api/v1/runs/{waiting['id']}/pending-actions")
    assert len(pending["items"]) == 1 and pending["items"][0]["kind"] == "client_tool"
    thread = await live.thread(waiting["thread_id"])
    assert waiting["sealed_state_digest_sha256"]
    return {
        "expected_thread_version": thread["version"],
        "sealed_state_digest_sha256": waiting["sealed_state_digest_sha256"],
        "resolutions": [{"call_id": pending["items"][0]["call_id"], "action": "complete", "result": result}],
    }
