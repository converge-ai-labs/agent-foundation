"""Recognize a rejected subscription grant while preserving the provider's refresh lifecycle."""

import httpx2
from a13n_harness.models.codex import CodexRequestModel
from pydantic_ai.providers.openai_codex import CredentialsRefreshError


class CodexRefreshGrantRejected(CredentialsRefreshError):
    """The token endpoint explicitly requires a new grant for this account."""


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
        raise CodexRefreshGrantRejected("The Codex authorization is no longer valid. Sign in again.")


class SubscriptionCodexModel(CodexRequestModel):
    async def _capture_response(self, response: httpx2.Response) -> None:
        await check_codex_refresh_response(response)
        await super()._capture_response(response)
