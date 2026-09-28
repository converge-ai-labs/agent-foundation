"""A signed-in session on the local Service's public HTTP API, for seeding and private resources."""

from __future__ import annotations

import time
from collections.abc import Callable
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx2

type Json = dict[str, Any]

SEALED = frozenset({"waiting", "completed", "failed", "cancelled"})


class ApiError(RuntimeError):
    pass


class Api:
    """One principal's session; threads may share it."""

    def __init__(self, base_url: str) -> None:
        self._http = httpx2.Client(base_url=base_url, trust_env=False, timeout=30)

    def __enter__(self) -> Api:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        self._http.close()

    @property
    def base_url(self) -> str:
        return str(self._http.base_url)

    @property
    def workspace_id(self) -> str:
        """The workspace business routes act in, which a login session names in `X-Workspace-ID` on every request;
        management routes ignore it."""
        return self._http.headers["x-workspace-id"]

    @workspace_id.setter
    def workspace_id(self, workspace_id: str) -> None:
        self._http.headers["x-workspace-id"] = workspace_id

    def first_workspace(self) -> Json:
        """The workspace bootstrap created with the organization: the oldest one the caller can read."""
        return min(self.items("/api/v1/workspaces"), key=lambda workspace: workspace["created_at"])

    def login(self, email: str, password: str) -> str:
        """Sign in; returns the principal ID."""
        return self._adopt(self._send("POST", "/api/v1/auth/login", json={"email": email, "password": password}))

    def accept_invitation(self, invitation_url: str, password: str, name: str) -> str:
        """Accept a manually delivered invitation link, which signs in as the invitee; returns the principal ID."""
        link = urlsplit(invitation_url)
        body = {"token": link.fragment.removeprefix("token="), "password": password, "name": name}
        return self._adopt(self._send("POST", "/api/v1" + link.path, json=body))

    def get(self, path: str) -> Json:
        return self._send("GET", path).json()

    def content(self, path: str) -> bytes:
        return self._send("GET", path).content

    def items(self, path: str, **filters: str) -> list[Json]:
        """Every item of a collection, across pages."""
        found: list[Json] = []
        cursor: str | None = None
        while True:
            params = {"limit": 100, **filters, **({"cursor": cursor} if cursor else {})}
            page = self._send("GET", path, params=params).json()
            found += page["items"]
            if not (cursor := page["next_cursor"]):
                return found

    def post(
        self,
        path: str,
        body: Json | None = None,
        *,
        current: Json | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        idempotent: bool = False,
    ) -> Json:
        """`current` is the resource a command changes, sent as its ETag; `idempotent` sends the
        Idempotency-Key that commands creating work require."""
        headers = {**_if_match(current), **({"idempotency-key": uuid4().hex} if idempotent else {})}
        return self._send("POST", path, json=body, files=files, headers=headers).json()

    def patch(self, path: str, current: Json, body: Json) -> Json:
        """Update `current`, the resource as last read, under its ETag."""
        return self._send("PATCH", path, json=body, headers=_if_match(current)).json()

    def put(self, path: str, current: Json, body: Json | bytes) -> Json:
        """Replace under `current`'s ETag: a JSON body, or the PNG bytes of an image route."""
        if isinstance(body, bytes):
            headers = {**_if_match(current), "content-type": "image/png"}
            return self._send("PUT", path, content=body, headers=headers).json()
        return self._send("PUT", path, json=body, headers=_if_match(current)).json()

    def delete(self, path: str, current: Json) -> None:
        self._send("DELETE", path, headers=_if_match(current))

    def until(self, path: str, reached: Callable[[Json], bool], timeout: float = 60) -> Json:
        """Poll `path` until `reached` holds for the resource."""
        deadline = time.monotonic() + timeout
        while not reached(resource := self.get(path)):
            if time.monotonic() > deadline:
                raise ApiError(f"{path} did not reach the expected state within {timeout:.0f}s")
            time.sleep(0.1)
        return resource

    def sealed_run(self, run_id: str, timeout: float = 60) -> Json:
        return self.until(f"/api/v1/runs/{run_id}", lambda run: run["status"] in SEALED, timeout)

    def _adopt(self, response: httpx2.Response) -> str:
        # The session cookie is Secure, which a cookie jar withholds over plain HTTP; loopback sends it explicitly.
        self._http.headers["cookie"] = "; ".join(f"{name}={value}" for name, value in response.cookies.items())
        signed_in = response.json()
        self._http.headers["x-csrf-token"] = signed_in["csrf_token"]
        return signed_in["principal_id"]

    def _send(self, method: str, path: str, **options: Any) -> httpx2.Response:
        response = self._http.request(method, path, **options)
        if response.is_error:
            # Service error envelopes never echo submitted values.
            raise ApiError(f"{method} {path} failed ({response.status_code}): {response.text[:1000]}")
        return response


def _if_match(current: Json | None) -> dict[str, str]:
    if current is None:
        return {}
    # Models are identified by key alone; every other resource by ID.
    identifier = current["id"] if "id" in current else current["key"]
    return {"if-match": f'"{identifier}:{current["version"]}"'}
