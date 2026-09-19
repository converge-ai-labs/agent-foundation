"""Public HTTP model setup plus file-based observations from the owned upstream."""

import json
from uuid import uuid4

from ..infrastructure.client import agent_input
from ..infrastructure.management_support import ManagementJourney
from ..infrastructure.round_two_lab import private_json


class ModelJourney(ManagementJourney):
    def __init__(self, lab, origin):
        super().__init__(lab)
        self.origin = origin

    async def model(
        self,
        *,
        provider_type="openai",
        api="openai.chat_completions",
        settings=None,
        configuration=None,
        credential="fixture-primary",
        headers=None,
        **plan,
    ):
        case_id = uuid4().hex
        directory = self.lab.root / "model-peer" / case_id
        directory.mkdir(mode=0o700)
        case = {"directory": directory, "answer": "MODEL_" + uuid4().hex}
        self.plan_model(case, **plan)
        config = {"base_url": self.origin + f"/{case_id}/v1", **(configuration or {})}
        provider = await self.post(
            self.base + "/model-providers",
            {
                "type": provider_type,
                "name": "Model E2E " + case_id,
                "configuration": config,
                "credential": {"api_key": credential} if credential is not None else None,
                "extra_headers": headers or {},
            },
        )
        case["provider"] = provider
        case["model"] = await self.post(
            self.base + "/models",
            {
                "key": "model-" + case_id,
                "name": "Model E2E " + case_id,
                "provider_id": provider["id"],
                "upstream_model": "manual-model",
                "model_api": api,
                "settings": settings or {},
            },
        )
        return case

    def plan_model(self, case, **values):
        path = case["directory"] / "plan.json"
        previous = json.loads(path.read_text()) if path.exists() else {"answer": case["answer"]}
        private_json(path, {**previous, **values})

    def requests(self, case, *, inference=False):
        path = case["directory"] / "requests.jsonl"
        values = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [item for item in values if item["method"] == "POST"] if inference else values

    async def invoke(self, case, *, agent=None, overrides=None, expected="completed"):
        agent = agent or await self.agent(model_key=case["model"]["key"])
        body = {"agent_id": agent["agent"]["id"], "input": agent_input("Reply with the requested proof.")}
        if overrides is not None:
            body["config_override"] = overrides
        receipt = await self.post(self.base + "/runs", body, expected=202)
        self.live.track(receipt)
        return await self.live.finish(receipt["run_id"], expected)

    async def gated(self, case, receipt):
        async def observed():
            return (case["directory"] / "ready").exists()

        await self.live.wait(observed, bool, "upstream request gate")
        assert (await self.live.run(receipt["run_id"]))["status"] == "running"

    def release_model(self, case):
        (case["directory"] / "release").touch()
