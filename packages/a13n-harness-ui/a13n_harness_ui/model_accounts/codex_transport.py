"""Recognize a rejected subscription grant while preserving the provider's refresh lifecycle."""

from hashlib import sha256
from urllib.parse import parse_qs

import httpx2
from a13n_harness.models.codex import CodexRequestModel
from pydantic_ai.providers.openai_codex import CredentialsRefreshError

from .models import CODEX_SIGN_IN_REQUIRED


class CodexRefreshGrantRejected(CredentialsRefreshError):
    """The token endpoint explicitly requires a new grant for this account."""

    def __init__(self, fingerprint: str) -> None:
        super().__init__(CODEX_SIGN_IN_REQUIRED)
        self.fingerprint = fingerprint

    def __reduce__(self) -> tuple[type, tuple[str]]:
        return type(self), (self.fingerprint,)


def grant_fingerprint(refresh_token: str) -> str:
    return sha256(refresh_token.encode()).hexdigest()


async def check_codex_refresh_response(response: httpx2.Response) -> None:
    """Read the structured recovery signal before the SDK flattens the error body."""
    if (
        response.status_code not in (400, 401)
        or response.request.method != "POST"
        or response.url != "https://auth.openai.com/oauth/token"
    ):
        return
    await response.aread()
    try:
        body = response.json()
    except ValueError:
        return
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else error
    if code in ("invalid_grant", "refresh_token_invalidated", "refresh_token_expired", "refresh_token_reused"):
        # The official SDK sends a buffered form. Bind the signal to the grant
        # that actually went over the wire, never the caller's earlier snapshot.
        form = parse_qs(response.request.content.decode("ascii"))
        if form.get("grant_type") == ["refresh_token"] and len(tokens := form.get("refresh_token", [])) == 1:
            raise CodexRefreshGrantRejected(grant_fingerprint(tokens[0]))


class SubscriptionCodexModel(CodexRequestModel):
    async def _capture_response(self, response: httpx2.Response) -> None:
        await check_codex_refresh_response(response)
        await super()._capture_response(response)
