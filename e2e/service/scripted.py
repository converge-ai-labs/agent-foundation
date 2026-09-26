"""Controls of the scripted model process, and what its recorded requests show the model was asked."""

from typing import Any

import httpx2

from .api import eventually, expect

# User parts the Harness adds to every request: the environment snapshot and the run identity.
HARNESS_CONTEXT = ("Current Environment mounts (trusted dynamic context)", "<agent-context ")


def user_texts(request: dict) -> list[str]:
    """The conversation's user texts in a model request, in order, without the context the Harness adds."""
    texts = []
    for entry in request["body"]["messages"]:
        if entry["role"] != "user":
            continue
        content = entry["content"]
        parts = [content] if isinstance(content, str) else [part.get("text", "") for part in content]
        texts.extend(part for part in parts if not part.startswith(HARNESS_CONTEXT))
    return texts


def tool_results(request: dict) -> list[str]:
    return [str(entry["content"]) for entry in request["body"]["messages"] if entry["role"] == "tool"]


class ScriptedModel:
    """Controls of the scripted model process: script turns, open gates and read what the model was asked."""

    def __init__(self, url: str):
        self.url = url
        self.base_url = url + "/v1"
        self.client = httpx2.AsyncClient(base_url=url, trust_env=False, timeout=10)

    async def say(self, text: str, **turn: Any) -> None:
        expect(await self.client.post("/fixture/turns", json={"text": text, **turn}), 200)

    async def call(self, name: str, arguments: dict[str, Any], *, call_id: str, **turn: Any) -> None:
        call = {"id": call_id, "name": name, "arguments": arguments}
        expect(await self.client.post("/fixture/turns", json={"tool_calls": [call], **turn}), 200)

    async def open(self, gate: str) -> None:
        expect(await self.client.post(f"/fixture/gates/{gate}"), 200)

    async def requests(self, marker: str | None = None) -> list[dict]:
        params = {"marker": marker} if marker is not None else {}
        return expect(await self.client.get("/fixture/requests", params=params), 200)["items"]

    async def arrived(self, marker: str, count: int = 1, *, status: str | None = None) -> list[dict]:
        """Wait until `count` requests carrying `marker` arrived, optionally all with `status`, and return them."""

        async def check() -> list[dict] | None:
            found = await self.requests(marker)
            if status is not None:
                found = [item for item in found if item["status"] == status]
            return found if len(found) >= count else None

        return await eventually(check)

    async def aclose(self) -> None:
        await self.client.aclose()
