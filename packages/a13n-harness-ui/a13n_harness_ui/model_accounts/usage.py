"""Typed Codex subscription usage and explicitly confirmed reset-credit redemption."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from uuid import UUID

import httpx2
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from pydantic_ai.providers.openai_codex import OpenAICodexCredentialSource


from .codex import BoundCodexCredentialSource

_BASE_URL = "https://chatgpt.com/backend-api/wham"


class _ProviderView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class UsageWindow(_ProviderView):
    used_percent: int
    limit_window_seconds: int = Field(ge=1)
    reset_at: int = Field(ge=0)


class UsageLimits(_ProviderView):
    allowed: bool
    limit_reached: bool
    primary_window: UsageWindow | None = None
    secondary_window: UsageWindow | None = None


class ResetCredit(_ProviderView):
    id: str = Field(min_length=1, max_length=256)
    reset_type: str
    status: str
    expires_at: str | None = None
    title: str | None = None
    description: str | None = None


class ResetCredits(_ProviderView):
    available_count: int = Field(ge=0)
    credits: tuple[ResetCredit, ...] = Field(default=(), max_length=256)


class CodexUsage(_ProviderView):
    account_id: str
    plan_type: str
    rate_limit: UsageLimits | None = None
    reset_credits: ResetCredits | None = None
    reset_unavailable: str | None = None


class ResetRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    account_id: str = Field(min_length=1, max_length=256)
    credit_id: str = Field(min_length=1, max_length=256)
    redeem_request_id: UUID


class ResetResult(_ProviderView):
    code: Literal["reset", "nothing_to_reset", "no_credit", "already_redeemed"]
    windows_reset: int = Field(default=0, ge=0)


class CodexUsageClient:
    """One account operation; the caller owns the async HTTP client lifetime."""

    def __init__(
        self,
        source: OpenAICodexCredentialSource,
        client: httpx2.AsyncClient,
        *,
        expected_account_id: str | None = None,
    ) -> None:
        from pydantic_ai.providers.openai_codex import OpenAICodexProvider

        self.client = client
        self.client.follow_redirects = False
        self.client.headers["User-Agent"] = "a13n-harness-ui"
        # The official provider installs its host-scoped auth on this dedicated
        # client. Account operations use HTTP directly, not the SDK retry layer.
        self._provider = OpenAICodexProvider(
            credential_source=BoundCodexCredentialSource(source, account_id=expected_account_id),
            http_client=client,
        )

    async def _request(
        self, method: str, path: str, *, body: dict[str, str] | None = None
    ) -> tuple[dict[str, object], str]:
        from pydantic_ai.providers.openai_codex import CredentialsPersistenceError, CredentialsRefreshError

        try:
            async with self.client.stream(method, _BASE_URL + path, json=body, timeout=10) as response:
                response.raise_for_status()
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 512 * 1024:
                        raise ValueError("Codex usage response exceeded the supported size.")
                import json

                value = json.loads(chunks)
                if not isinstance(value, dict):
                    raise ValueError("Codex returned an unsupported usage response.")
                return value, response.request.headers["ChatGPT-Account-Id"]
        except (CredentialsRefreshError, CredentialsPersistenceError):
            # Upstream token errors may include provider response text. Account
            # diagnostics retain the outcome category, never that raw payload.
            raise ValueError(
                "Codex credentials could not be refreshed or saved. Check the shared account store before retrying."
            ) from None
        except httpx2.HTTPStatusError as exc:
            raise ValueError(
                f"Codex account API returned HTTP {exc.response.status_code}. "
                + ("Reset outcome is unconfirmed; preserve the redemption ID. " if method == "POST" else "")
                + "No automatic retry occurred."
            ) from None
        except httpx2.HTTPError:
            raise ValueError(
                "Codex account API could not confirm the outcome. Retry explicitly; a reset retry must reuse the same redemption ID."
            ) from None

    async def read(self) -> CodexUsage:
        value, account_id = await self._request("GET", "/usage")
        credits = None
        diagnostic = None
        try:
            raw_credits, credit_account = await self._request("GET", "/rate-limit-reset-credits")
            if credit_account != account_id:
                raise ValueError("Codex account changed during the usage query; refresh before confirming a reset.")
            credits = ResetCredits.model_validate(raw_credits)
        except ValueError as exc:
            diagnostic = str(exc)
        return CodexUsage.model_validate(
            {
                "account_id": account_id,
                "plan_type": value.get("plan_type"),
                "rate_limit": value.get("rate_limit"),
                "reset_credits": credits,
                "reset_unavailable": diagnostic,
            }
        )

    async def redeem(self, request: ResetRequest) -> ResetResult:
        value, _ = await self._request(
            "POST",
            "/rate-limit-reset-credits/consume",
            body={
                "credit_id": request.credit_id,
                "redeem_request_id": str(request.redeem_request_id),
            },
        )
        return ResetResult.model_validate(value)
