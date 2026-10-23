"""Provider-specific subscription allowance reads using the existing OAuth lifecycle."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

import httpx2
from a13n_harness.providers.model.oauth.copilot import CopilotCredentials, refresh_copilot_credentials
from a13n_harness.providers.model.oauth.grok import refresh_grok_credentials
from a13n_harness.providers.model.oauth.models import GrokCredentials, ModelAuthenticationError
from pydantic import BaseModel, ConfigDict, Field

from .copilot import CopilotAccountStore
from .grok import GrokAccountStore

_GROK_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
_COPILOT_URL = "https://api.github.com/copilot_internal/user"


class _View(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


class GrokMoney(_View):
    # Proto3 omits zero values. An absent money object remains unknown.
    val: int = 0


class GrokPeriod(_View):
    type: str | None = None
    start: str | None = None
    end: str | None = None


class GrokBilling(_View):
    credit_usage_percent: float | None = Field(default=None, validation_alias="creditUsagePercent")
    current_period: GrokPeriod | None = Field(default=None, validation_alias="currentPeriod")
    prepaid_balance: GrokMoney | None = Field(default=None, validation_alias="prepaidBalance")
    is_unified_billing_user: bool | None = Field(default=None, validation_alias="isUnifiedBillingUser")
    on_demand_cap: GrokMoney | None = Field(default=None, validation_alias="onDemandCap")
    on_demand_used: GrokMoney | None = Field(default=None, validation_alias="onDemandUsed")
    monthly_limit: GrokMoney | None = Field(default=None, validation_alias="monthlyLimit")
    used: GrokMoney | None = None
    billing_period_end: str | None = Field(default=None, validation_alias="billingPeriodEnd")

    @property
    def usage_percent(self) -> float | None:
        if self.credit_usage_percent is not None:
            return self.credit_usage_percent
        if self.monthly_limit is not None and self.monthly_limit.val > 0 and self.used is not None:
            return self.used.val / self.monthly_limit.val * 100
        return None


class GrokUsage(_View):
    provider: Literal["grok"] = "grok"
    config: GrokBilling | None = None


class CopilotQuota(_View):
    quota_id: str | None = None
    entitlement: float | None = None
    remaining: float | None = None
    unlimited: bool = False
    overage_count: float | None = None
    overage_permitted: bool | None = None
    percent_remaining: float | None = None


class CopilotUsage(_View):
    copilot_plan: str | None = None
    quota_reset_date: str | None = None
    quota_snapshots: dict[str, CopilotQuota] | None = None


async def _read_json(client: httpx2.AsyncClient, url: str, provider: str) -> bytes:
    try:
        async with client.stream("GET", url, timeout=15) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 512 * 1024:
                    raise ValueError(f"{provider} usage response exceeded the supported size.")
            return bytes(content)
    except httpx2.HTTPStatusError as exc:
        raise ValueError(f"{provider} usage API returned HTTP {exc.response.status_code}.") from None
    except (httpx2.HTTPError, ModelAuthenticationError):
        raise ValueError(f"{provider} usage could not be refreshed. Check the selected account login.") from None


async def read_grok_usage(
    source: GrokAccountStore, client: httpx2.AsyncClient, *, expected_account_id: str | None = None
) -> GrokUsage:
    from a13n_harness.providers.model.oauth.runtime import _GrokAuthentication, _OAuthAuth

    class GrokBillingAuth(_OAuthAuth[GrokCredentials]):
        def _apply(self, request: httpx2.Request, credentials: GrokCredentials) -> None:
            if expected_account_id is not None and credentials.account_id != expected_account_id:
                raise ValueError("The Grok account changed. Refresh the account before reading usage.")
            super()._apply(request, credentials)
            request.headers["x-userid"] = credentials.account_id
            request.headers["X-XAI-Token-Auth"] = "xai-grok-cli"

        def _strip_protected_headers(self, request: httpx2.Request) -> None:
            super()._strip_protected_headers(request)
            request.headers.pop("x-userid", None)
            request.headers.pop("X-XAI-Token-Auth", None)

    async def refresh(credentials: GrokCredentials) -> GrokCredentials:
        return await refresh_grok_credentials(credentials, http_client=client)

    manager = _GrokAuthentication(source, refresh, timedelta(minutes=5))
    auth = GrokBillingAuth(manager, base_url=_GROK_URL, client=client)
    client.auth = auth
    client.follow_redirects = False
    client.headers["User-Agent"] = "a13n-harness-ui"
    client.headers["x-grok-client-mode"] = "a13n-harness-ui"
    client.event_hooks["response"].append(auth.protect_redirect)
    return GrokUsage.model_validate_json(await _read_json(client, _GROK_URL, "Grok"))


async def read_copilot_usage(source: CopilotAccountStore, client: httpx2.AsyncClient) -> CopilotUsage:
    from a13n_harness.providers.model.oauth.copilot_runtime import _CopilotAuthentication
    from a13n_harness.providers.model.oauth.runtime import _OAuthAuth

    class CopilotQuotaAuth(_OAuthAuth[CopilotCredentials]):
        def _apply(self, request: httpx2.Request, credentials: CopilotCredentials) -> None:
            request.headers["Authorization"] = f"token {credentials.access_token}"

    async def refresh(credentials: CopilotCredentials) -> CopilotCredentials:
        return await refresh_copilot_credentials(credentials, http_client=client)

    manager = _CopilotAuthentication(source, refresh, timedelta(minutes=5))
    auth = CopilotQuotaAuth(manager, base_url=_COPILOT_URL, client=client)
    client.auth = auth
    client.follow_redirects = False
    client.headers.update({"User-Agent": "a13n-harness-ui", "X-GitHub-Api-Version": "2025-04-01"})
    client.event_hooks["response"].append(auth.protect_redirect)
    result = CopilotUsage.model_validate_json(await _read_json(client, _COPILOT_URL, "Copilot"))
    # A selected-source change while awaiting HTTP must not publish another account's quota.
    await manager.prepare()
    return result
