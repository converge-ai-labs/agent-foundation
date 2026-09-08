"""Bounded off-loop Argon2id work and high-entropy credential verifiers."""

import hashlib
import hmac
import secrets

from anyio import CapacityLimiter, to_thread
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def matches_token(value: str, verifier: str) -> bool:
    return hmac.compare_digest(token_hash(value), verifier)


def csrf_token(session_token: str) -> str:
    return hmac.new(session_token.encode(), b"a13n-session-csrf-v1", hashlib.sha256).hexdigest()


class Passwords:
    def __init__(self) -> None:
        self._hasher = PasswordHasher()
        self._limiter = CapacityLimiter(4)
        self._dummy_hash: str | None = None

    async def initialize(self) -> None:
        self._dummy_hash = await self.hash(new_token())

    async def hash(self, password: str) -> str:
        return await to_thread.run_sync(self._hasher.hash, password, limiter=self._limiter)

    async def verify(self, verifier: str | None, password: str) -> bool:
        stored = verifier or self._dummy_hash
        if stored is None:
            raise RuntimeError("Password verification was not initialized")

        def check() -> bool:
            try:
                return self._hasher.verify(stored, password)
            except (VerificationError, InvalidHashError):
                return False

        valid = await to_thread.run_sync(check, limiter=self._limiter)
        return valid and verifier is not None
