"""Host-owned exclusion and durable uncertain-grant evidence for the Grok file store."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path

from anyio import CancelScope, fail_after, sleep, to_thread
from filelock import FileLock, Timeout

from ._common import JsonSnapshot, read_json_snapshot, write_json_if_unchanged
from .models import AccountStoreError, Provider


@asynccontextmanager
async def store_lock(path: Path) -> AsyncIterator[None]:
    """One complete mutation/refresh interval, shared by cooperating processes and instances."""
    canonical = path.resolve()
    await to_thread.run_sync(lambda: canonical.parent.mkdir(mode=0o700, parents=True, exist_ok=True))
    lock = FileLock(f"{canonical}.refresh.lock", timeout=0, thread_local=False, mode=0o600)
    acquired = False
    try:
        with fail_after(30):
            while not acquired:
                try:
                    await to_thread.run_sync(lock.acquire)
                    acquired = True
                except Timeout:
                    await sleep(0.05)
        yield
    finally:
        if acquired:
            with CancelScope(shield=True):
                await to_thread.run_sync(lock.release)


def grant_fingerprint(refresh_token: str | None) -> str:
    # Only a one-way fingerprint of a high-entropy grant enters host coordination state.
    return sha256((refresh_token or "").encode()).hexdigest()


class RefreshJournal:
    def __init__(self, path: Path, scope: str):
        self.path = Path(f"{path.resolve()}.refresh.json")
        self.scope = scope

    async def blocked(self, grant: str) -> bool:
        state = await self._read()
        return grant in state.document.get("scopes", {}).get(self.scope, []) if state.document else False

    async def set_blocked(self, grant: str, blocked: bool) -> None:
        state = await self._read()
        document = dict(state.document or {"version": 1, "scopes": {}})
        scopes = dict(document["scopes"])
        grants = set(scopes.get(self.scope, []))
        if blocked:
            grants.add(grant)
        else:
            grants.discard(grant)
        scopes[self.scope] = sorted(grants)
        document["scopes"] = scopes
        await write_json_if_unchanged(self.path, document, state.digest, provider=Provider.GROK)

    async def _read(self) -> JsonSnapshot:
        state = await read_json_snapshot(self.path, provider=Provider.GROK)
        doc = state.document
        if doc is not None and (
            doc.get("version") != 1
            or not isinstance(doc.get("scopes"), dict)
            or any(
                not isinstance(key, str)
                or not isinstance(value, list)
                or any(not isinstance(item, str) or len(item) != 64 for item in value)
                for key, value in doc["scopes"].items()
            )
        ):
            raise AccountStoreError(
                "Grok refresh coordination state is invalid.", code="account_store_incompatible", provider=Provider.GROK
            )
        return state
