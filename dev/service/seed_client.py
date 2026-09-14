"""Authenticated API client shared by local seed journeys."""

import re
from collections.abc import AsyncIterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import httpx2


class Client:
    def __init__(self, http: httpx2.AsyncClient):
        self.http = http

    async def request(self, method: str, path: str, *, expected: int = 200, **kwargs) -> Any:
        headers = {"Idempotency-Key": uuid4().hex, **kwargs.pop("headers", {})}
        response = await self.http.request(method, path, headers=headers, **kwargs)
        if response.status_code != expected:
            code = "unknown"
            reason = ""
            try:
                error = response.json().get("error", {})
                code = error.get("code", "unknown")
                value = error.get("details", {}).get("reason", "") if isinstance(error.get("details"), dict) else ""
                if isinstance(value, str) and re.fullmatch(r"[a-z_]{1,96}", value):
                    reason = f", {value}"
            except ValueError:
                pass
            raise RuntimeError(f"Seed request {method} {path} failed: HTTP {response.status_code} ({code}{reason})")
        return response.json() if response.content else None

    async def collection(self, path: str, *, params: dict | None = None) -> list[dict]:
        result = []
        async for page in self.pages(path, params=params):
            result.extend(page["items"])
        return result

    async def pages(self, path: str, *, params: dict | None = None) -> AsyncIterator[dict]:
        cursor = None
        for _ in range(100):
            page = await self.request("GET", path, params={**(params or {}), **({"cursor": cursor} if cursor else {})})
            yield page
            cursor = page.get("next_cursor")
            if cursor is None:
                return
        raise RuntimeError("Seed collection exceeded its pagination bound")

    async def etag(self, path: str) -> dict[str, str]:
        response = await self.http.get(path)
        response.raise_for_status()
        return {"If-Match": response.headers["ETag"]}

    @contextmanager
    def scope(self, workspace_id: str | None):
        name = "X-A13N-Workspace-ID"
        previous = self.http.headers.pop(name, None)
        if workspace_id is not None:
            self.http.headers[name] = workspace_id
        try:
            yield
        finally:
            self.http.headers.pop(name, None)
            if previous is not None:
                self.http.headers[name] = previous
