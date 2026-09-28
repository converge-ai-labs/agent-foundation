"""The administrator's workspace over HTTPS: the public API calls the journeys make, and their waits."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import httpx2

SEALED = frozenset({"waiting", "completed", "failed", "cancelled"})
TIMEOUT = 30


async def eventually[T](check: Callable[[], Awaitable[T | None]], *, timeout: float = TIMEOUT) -> T:
    """Poll `check` until it returns a value other than None or False."""
    async with asyncio.timeout(timeout):
        while not (value := await check()):
            await asyncio.sleep(0.1)
    return value


def expect(response: httpx2.Response, status: int) -> Any:
    assert response.status_code == status, f"{response.request.method} {response.request.url}: {response.text}"
    return response.json() if response.content else None


def message(agent: dict, text: str, **fields: Any) -> dict[str, Any]:
    return {"agent_id": agent["id"], "payload": {"content": [{"type": "text", "text": text}]}, **fields}


class Workspace:
    """The journey's workspace as its administrator sees it through the public API.

    The client's login session names the workspace in `X-Workspace-ID`, so business routes such as
    `/api/v1/threads` act in it; `path` addresses the workspace's own management routes.
    """

    def __init__(self, client: httpx2.AsyncClient, tenant: dict[str, str]):
        self.client, self.tenant = client, tenant
        self.path = f"/api/v1/workspaces/{tenant['workspace_id']}"

    async def create_model(self, base_url: str) -> str:
        """A model the scripted model process serves; returns its key, which defaults to the upstream name."""
        provider = await self.client.post(
            "/api/v1/model-providers",
            json={
                "type": "openai",
                "name": "Scripted",
                "config": {"base_url": base_url},
                "credential": {"api_key": "sk-live-journey"},
            },
        )
        model = await self.client.post(
            "/api/v1/models",
            json={
                "provider_id": expect(provider, 201)["id"],
                "name": "Scripted",
                "config": {"model_name": "scripted", "model_api": "openai.chat_completions"},
            },
        )
        return expect(model, 201)["key"]

    async def create_agent(self, name: str, model: str, **config: Any) -> dict:
        response = await self.client.post("/api/v1/agents", json={"name": name, "config": {"model": model, **config}})
        return expect(response, 201)

    async def start(self, agent: dict, text: str, *, key: str | None = None, **fields: Any) -> dict:
        """A new thread with its first message; the receipt carries the thread, the entry and the run."""
        response = await self.client.post(
            "/api/v1/threads", json=message(agent, text, **fields), headers={"idempotency-key": key or uuid4().hex}
        )
        return expect(response, 201)

    async def send(self, thread_id: str, agent: dict, text: str, *, key: str | None = None, **fields: Any) -> dict:
        response = await self.client.post(
            f"/api/v1/threads/{thread_id}/inbox",
            json=message(agent, text, **fields),
            headers={"idempotency-key": key or uuid4().hex},
        )
        return expect(response, 201)

    async def resume(self, run_id: str, answers: list[dict], *, key: str) -> httpx2.Response:
        return await self.client.post(
            f"/api/v1/runs/{run_id}/resume", json={"answers": answers}, headers={"idempotency-key": key}
        )

    async def interrupt(self, run_id: str) -> httpx2.Response:
        return await self.client.post(f"/api/v1/runs/{run_id}/interrupt")

    async def thread(self, thread_id: str) -> dict:
        return expect(await self.client.get(f"/api/v1/threads/{thread_id}"), 200)

    async def run(self, run_id: str) -> dict:
        return expect(await self.client.get(f"/api/v1/runs/{run_id}"), 200)

    async def runs(self, thread_id: str) -> list[dict]:
        """The thread's runs, oldest first."""
        listing = expect(await self.client.get(f"/api/v1/threads/{thread_id}/runs", params={"limit": 100}), 200)
        return listing["items"][::-1]

    async def items(self, run_id: str) -> dict:
        return expect(await self.client.get(f"/api/v1/runs/{run_id}/items"), 200)

    async def attempts(self, run_id: str) -> list[dict]:
        return expect(await self.client.get(f"/api/v1/runs/{run_id}/attempts"), 200)["items"]

    async def inbox(self, thread_id: str) -> list[dict]:
        listing = await self.client.get(f"/api/v1/threads/{thread_id}/inbox", params={"limit": 100})
        return expect(listing, 200)["items"]

    async def sealed(self, run_id: str, *, timeout: float = TIMEOUT) -> dict:
        async def check() -> dict | None:
            run = await self.run(run_id)
            return run if run["status"] in SEALED else None

        return await eventually(check, timeout=timeout)

    async def next_run(self, thread_id: str, after: str) -> dict:
        """The run the thread started after `after`, once it exists."""

        async def check() -> dict | None:
            thread = await self.thread(thread_id)
            current = thread["current_run_id"] or thread["last_run_id"]
            return await self.run(current) if current not in (None, after) else None

        return await eventually(check)


def transcript(items: dict) -> list[tuple[str, str]]:
    """The displayed text messages of a run's committed items as (role, text)."""
    return [
        (item["content"].get("role"), item["content"]["text"])
        for item in items["items"]
        if item["kind"] == "text_message" and item["content"].get("metadata", {}).get("display", True)
    ]
