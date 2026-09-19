"""The shared Grok grant protocol behaves the same over any Host exclusion and journal."""

from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from a13n_harness.providers.model.oauth import (
    CredentialPersistenceError,
    GrokCredentials,
    ModelAuthenticationError,
    RefreshNotDispatched,
)
from a13n_harness.providers.model.oauth.rotation import grant_fingerprint, rotate_grok_grant

pytestmark = pytest.mark.anyio


def credential(refresh: str | None, *, account: str = "account-1") -> GrokCredentials:
    return GrokCredentials(
        account_id=account,
        auth_mode="oidc",
        create_time=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        issuer="https://accounts.example",
        client_id="client-1",
        access_token=f"access-{refresh}",
        refresh_token=refresh,
    )


class FakeStore:
    """The smallest Host: an in-memory document, an entry counter and a grant journal."""

    def __init__(self, current: GrokCredentials, *, fail_publish: bool = False) -> None:
        self.current = current
        self.published: list[GrokCredentials] = []
        self.blocked_grants: set[str] = set()
        self.entered = 0
        self._fail_publish = fail_publish

    def exclusive(self) -> AbstractAsyncContextManager[None]:
        @asynccontextmanager
        async def guard():
            self.entered += 1
            yield

        return guard()

    async def read(self) -> tuple[GrokCredentials, int]:
        return self.current, self.entered

    async def publish(self, state: int, credentials: GrokCredentials) -> None:
        del state
        if self._fail_publish:
            raise OSError("disk full")
        self.published.append(credentials)
        self.current = credentials

    async def blocked(self, grant: str) -> bool:
        return grant in self.blocked_grants

    async def set_blocked(self, grant: str, blocked: bool) -> None:
        if blocked:
            self.blocked_grants.add(grant)
        else:
            self.blocked_grants.discard(grant)


async def test_rotation_publishes_once_and_clears_a_grant_that_was_not_rotated():
    store = FakeStore(credential("refresh-1"))

    async def exchange(current):
        assert store.blocked_grants == {grant_fingerprint(current.refresh_token)}
        return credential("refresh-1")

    result = await rotate_grok_grant(store, store.current, exchange)

    assert result.access_token == "access-refresh-1" and store.published == [result]
    assert store.blocked_grants == set() and store.entered == 1


async def test_a_rotated_grant_stays_blocked_because_the_old_one_was_spent():
    store = FakeStore(credential("refresh-1"))
    spent = grant_fingerprint("refresh-1")

    result = await rotate_grok_grant(store, store.current, lambda current: _returns(credential("refresh-2")))

    assert result.refresh_token == "refresh-2" and store.blocked_grants == {spent}


async def test_a_proven_predispatch_failure_releases_the_grant():
    store = FakeStore(credential("refresh-1"))

    async def not_dispatched(current):
        raise RefreshNotDispatched("grok", "no token was sent")

    with pytest.raises(RefreshNotDispatched):
        await rotate_grok_grant(store, store.current, not_dispatched)
    assert store.blocked_grants == set()


async def test_a_failed_publication_keeps_the_grant_blocked():
    store = FakeStore(credential("refresh-1"), fail_publish=True)

    with pytest.raises(CredentialPersistenceError):
        await rotate_grok_grant(store, store.current, lambda current: _returns(credential("refresh-2")))
    assert store.blocked_grants == {grant_fingerprint("refresh-1")}


async def test_an_uncertain_grant_is_refused_before_any_exchange():
    store = FakeStore(credential("refresh-1"))
    store.blocked_grants.add(grant_fingerprint("refresh-1"))

    with pytest.raises(ModelAuthenticationError, match="unknown"):
        await rotate_grok_grant(store, store.current, lambda current: _returns(credential("refresh-2")))
    assert store.published == []


async def test_a_changed_account_or_credential_is_adopted_or_refused_before_dispatch():
    store = FakeStore(credential("refresh-2"))
    stale = credential("refresh-1")

    adopted = await rotate_grok_grant(store, stale, lambda current: _returns(credential("refresh-3")))
    assert adopted.refresh_token == "refresh-2" and store.published == []

    store.current = credential("refresh-2", account="account-2")
    with pytest.raises(ModelAuthenticationError, match="account changed"):
        await rotate_grok_grant(store, stale, lambda current: _returns(credential("refresh-3")))


async def _returns(value: GrokCredentials) -> GrokCredentials:
    return value
