"""Cloud Browser account inputs and policy-bound supplier API access.

Supplier lifecycle reconciliation remains separate from this HTTP client. The client
never retries requests, follows redirects, or includes upstream details in errors.
"""

import re
from datetime import datetime
from typing import Literal
from urllib.parse import quote, urlsplit

import httpx2
from a13n_harness.capabilities.browser.cdp import Chromium
from a13n_harness.http import outbound_tls_verify
from a13n_harness.providers.endpoint_policy import EndpointPolicy, EndpointPolicyError
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.outbound import bound_response, validate_cdp_endpoint
from a13n_service.providers.browser.definition import BrowserProviderError, LiveBrowser, ViewerGrant


class BrowserbaseConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project_id: str = Field(min_length=1, max_length=256)


class KernelConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    organization_id: str = Field(min_length=1, max_length=256)
    project_id: str | None = Field(default=None, min_length=1, max_length=256)


class BrowserUseConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project_id: str = Field(min_length=1, max_length=256)


class CloudCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    api_key: SecretStr


class BrowserbaseRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    timeout: int = Field(default=300, ge=60, le=21600, title="Timeout seconds")


class KernelRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    timeout_seconds: int = Field(default=300, ge=10, le=259200)
    headless: bool = True


class BrowserUseRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    timeout_minutes: int = Field(default=5, ge=1, le=240)


class CloudAPI:
    """One authenticated client for one supplier and account."""

    def __init__(
        self,
        supplier: Literal["browserbase", "kernel", "browser_use"],
        credential: CloudCredential,
        policy: EndpointPolicy,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        max_bytes: int = 16777216,
    ) -> None:
        self.supplier = supplier
        self.policy = policy
        self.base = {
            "browserbase": "https://api.browserbase.com",
            "kernel": "https://api.onkernel.com",
            "browser_use": "https://api.browser-use.com",
        }[supplier]
        key = credential.api_key.get_secret_value()
        headers = {
            "browserbase": {"X-BB-API-Key": key},
            "kernel": {"Authorization": f"Bearer {key}"},
            "browser_use": {"X-Browser-Use-API-Key": key},
        }[supplier]

        async def check_response(response: httpx2.Response) -> None:
            bound_response(response, max_bytes)

        self.client = httpx2.AsyncClient(
            base_url=self.base,
            headers={**headers, "accept-encoding": "identity"},
            verify=outbound_tls_verify(),
            timeout=30,
            follow_redirects=False,
            transport=transport,
            event_hooks={"response": [check_response]},
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        params: dict | None = None,
        allow_missing: bool = False,
        create: bool = False,
    ) -> httpx2.Response:
        # Only fixed, provider-owned paths are accepted; callers cannot redirect credentials.
        if not path.startswith("/") or path.startswith("//"):
            raise BrowserProviderError("cloud_endpoint_invalid", certainty="not_dispatched")
        try:
            request = self.client.build_request(method, path, json=body, params=params)
            base = httpx2.URL(self.base)
            if (request.url.scheme, request.url.host, request.url.port) != (base.scheme, base.host, base.port):
                raise BrowserProviderError("cloud_endpoint_invalid", certainty="not_dispatched")
            await self.policy.validate(str(request.url))
        except (EndpointPolicyError, ValueError, httpx2.InvalidURL):
            raise BrowserProviderError("cloud_endpoint_denied", certainty="not_dispatched") from None
        try:
            response = await self.client.send(request)
        except (httpx2.HTTPError, ServiceError):
            raise BrowserProviderError("cloud_request_failed") from None
        if response.status_code >= 300 and not (allow_missing and response.status_code == 404):
            if create:
                certainty = "rejected" if response.status_code in {400, 401, 403} else "unknown"
            else:
                certainty = (
                    "unknown"
                    if response.status_code < 400
                    or response.status_code in {408, 409, 429}
                    or response.status_code >= 500
                    else "known"
                )
            raise BrowserProviderError("cloud_request_failed", certainty=certainty)
        return response

    async def close(self) -> None:
        await self.client.aclose()


async def _connect(api: CloudAPI, state: dict[str, JsonValue]) -> Chromium:
    url = state.get("cdp_url")
    if not isinstance(url, str):
        raise BrowserProviderError("cloud_invalid_response")
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "wss":
            raise EndpointPolicyError("cloud CDP requires secure WebSocket")
        await validate_cdp_endpoint(api.policy, url)
    except (EndpointPolicyError, ValueError):
        raise BrowserProviderError("cloud_endpoint_denied", certainty="not_dispatched") from None
    try:
        return await Chromium.connect(url, timeout=15, discover=False)
    except Exception:
        # Native transport errors can include the credential-bearing URL.
        raise BrowserProviderError("cloud_connect_failed") from None


class CloudUnsupportedOperations:
    """Operations not advertised by managed cloud providers."""

    async def stop(self, allocation_id: str, state: dict[str, JsonValue]) -> LiveBrowser:
        raise NotImplementedError("Cloud browser stop is not supported")

    async def start(self, allocation_id: str, state: dict[str, JsonValue], *, generation: int) -> LiveBrowser:
        raise NotImplementedError("Cloud browser start is not supported")

    async def ensure_viewer(
        self, state: dict[str, JsonValue], grant_id: str, *, episode: int, expires_at: datetime
    ) -> ViewerGrant:
        raise NotImplementedError("Cloud browser viewer is not supported")

    async def revoke_viewer(self, state: dict[str, JsonValue], grant_id: str, *, episode: int) -> None:
        raise NotImplementedError("Cloud browser viewer is not supported")

    async def upload_file(self, state: dict[str, JsonValue], name: str, content: bytes) -> str:
        raise NotImplementedError("Cloud browser files are not supported")

    async def download_file(self, state: dict[str, JsonValue], guid: str, *, max_bytes: int) -> bytes:
        raise NotImplementedError("Cloud browser files are not supported")


class Browserbase(CloudUnsupportedOperations):
    """One session per Service allocation, recovered by its exact metadata."""

    def __init__(self, config: BrowserbaseConfiguration, api: CloudAPI):
        self.config, self.api = config, api

    def _owned(self, allocation_id: str, item: dict) -> bool:
        metadata = item.get("userMetadata")
        return (
            item.get("projectId") == self.config.project_id
            and isinstance(metadata, dict)
            and metadata.get("a13n_allocation") == allocation_id
        )

    async def _find(self, allocation_id: str) -> dict | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", allocation_id):
            raise BrowserProviderError("cloud_invalid_allocation", certainty="not_dispatched")
        response = await self.api.request(
            "GET", "/v1/sessions", params={"q": f"user_metadata['a13n_allocation']:'{allocation_id}'"}
        )
        items = response.json()
        if not isinstance(items, list):
            raise BrowserProviderError("cloud_invalid_response")
        matches = [item for item in items if isinstance(item, dict) and self._owned(allocation_id, item)]
        if len(matches) > 1:
            raise BrowserProviderError("cloud_ambiguous_allocation")
        return matches[0] if matches else None

    async def _get(self, allocation_id: str, session_id: str) -> dict:
        response = await self.api.request("GET", f"/v1/sessions/{quote(session_id, safe='')}")
        item = response.json()
        if not isinstance(item, dict) or item.get("id") != session_id or not self._owned(allocation_id, item):
            raise BrowserProviderError("cloud_identity_mismatch")
        return item

    def _live(self, allocation_id: str, item: dict) -> LiveBrowser:
        if item.get("status") != "RUNNING":
            raise BrowserProviderError("cloud_session_not_ready")
        session_id = item.get("id")
        url = item.get("connectUrl")
        if not isinstance(session_id, str) or not isinstance(url, str):
            raise BrowserProviderError("cloud_invalid_response")
        expires_at = item.get("expiresAt")
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00")) if isinstance(expires_at, str) else None
        except ValueError:
            raise BrowserProviderError("cloud_invalid_response") from None
        return LiveBrowser(
            {"id": session_id, "allocation_id": allocation_id, "cdp_url": url}, expiry, runtime_id=session_id
        )

    async def create(self, allocation_id: str, recipe: BaseModel) -> LiveBrowser:
        if not isinstance(recipe, BrowserbaseRecipe):
            raise BrowserProviderError("cloud_invalid_recipe", certainty="not_dispatched")
        response = await self.api.request(
            "POST",
            "/v1/sessions",
            create=True,
            body={
                "projectId": self.config.project_id,
                "timeout": recipe.timeout,
                "keepAlive": True,
                "userMetadata": {"a13n_allocation": allocation_id},
            },
        )
        item = response.json()
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise BrowserProviderError("cloud_invalid_response")
        return self._live(allocation_id, await self._get(allocation_id, item["id"]))

    async def inspect(self, allocation_id: str, state: dict[str, JsonValue] | None) -> LiveBrowser | None:
        if state is None:
            item = await self._find(allocation_id)
            if item is None:
                return None
            item = await self._get(allocation_id, str(item["id"]))
        else:
            item = await self._get(allocation_id, str(state["id"]))
        if item.get("status") in {"ERROR", "TIMED_OUT", "COMPLETED"}:
            return None
        if item.get("status") == "PENDING":
            raise BrowserProviderError("cloud_session_not_ready")
        if item.get("status") != "RUNNING":
            raise BrowserProviderError("cloud_invalid_response")
        return self._live(allocation_id, item)

    async def delete(self, allocation_id: str, state: dict[str, JsonValue] | None) -> None:
        item = await self._find(allocation_id) if state is None else await self._get(allocation_id, str(state["id"]))
        if item is None or not isinstance(item.get("id"), str):
            raise BrowserProviderError("cloud_delete_unresolved")
        session_id = item["id"]
        if state is None:
            item = await self._get(allocation_id, session_id)
        if item.get("status") not in {"ERROR", "TIMED_OUT", "COMPLETED"}:
            await self.api.request(
                "POST",
                f"/v1/sessions/{quote(session_id, safe='')}",
                body={"projectId": self.config.project_id, "status": "REQUEST_RELEASE"},
            )
            item = await self._get(allocation_id, session_id)
        if item.get("status") not in {"ERROR", "TIMED_OUT", "COMPLETED"}:
            raise BrowserProviderError("cloud_delete_unresolved")

    async def connect(self, state: dict[str, JsonValue]) -> Chromium:
        return await _connect(self.api, state)

    async def close(self) -> None:
        await self.api.close()


class Kernel(CloudUnsupportedOperations):
    """Use a tagged session in a verified Kernel authorization scope."""

    def __init__(self, config: KernelConfiguration, api: CloudAPI):
        self.config, self.api = config, api

    async def _check_scope(self) -> None:
        response = await self.api.request("GET", "/auth/context")
        try:
            data = response.json()
            organization = data["organization"]["id"]
            project = data["authorization"]["effective_scope"]["project_id"]
        except (KeyError, TypeError, ValueError):
            raise BrowserProviderError("cloud_invalid_response") from None
        if organization != self.config.organization_id or project != self.config.project_id:
            raise BrowserProviderError("cloud_account_mismatch", certainty="not_dispatched")

    def _owned(self, allocation_id: str, item: dict) -> bool:
        tags = item.get("tags")
        return (
            item.get("name") == allocation_id
            and isinstance(tags, dict)
            and tags.get("a13n_allocation") == allocation_id
        )

    async def _get(self, allocation_id: str, session_id: str) -> dict:
        response = await self.api.request(
            "GET", f"/browsers/{quote(session_id, safe='')}", params={"include_deleted": "true"}
        )
        item = response.json()
        if not isinstance(item, dict) or item.get("session_id") != session_id or not self._owned(allocation_id, item):
            raise BrowserProviderError("cloud_identity_mismatch")
        return item

    def _live(self, allocation_id: str, item: dict) -> LiveBrowser:
        session_id, url = item.get("session_id"), item.get("cdp_ws_url")
        if not isinstance(session_id, str) or not isinstance(url, str):
            raise BrowserProviderError("cloud_invalid_response")
        return LiveBrowser({"id": session_id, "allocation_id": allocation_id, "cdp_url": url}, runtime_id=session_id)

    async def create(self, allocation_id: str, recipe: BaseModel) -> LiveBrowser:
        if not isinstance(recipe, KernelRecipe):
            raise BrowserProviderError("cloud_invalid_recipe", certainty="not_dispatched")
        try:
            await self._check_scope()
        except BrowserProviderError as exc:
            # Scope verification is read-only and happens before the create request.
            raise BrowserProviderError(exc.code, certainty="not_dispatched") from None
        response = await self.api.request(
            "POST",
            "/browsers",
            create=True,
            body={
                "name": allocation_id,
                "tags": {"a13n_allocation": allocation_id},
                "stealth": False,
                "headless": recipe.headless,
                "timeout_seconds": recipe.timeout_seconds,
            },
        )
        item = response.json()
        if not isinstance(item, dict) or not self._owned(allocation_id, item):
            raise BrowserProviderError("cloud_identity_mismatch")
        return self._live(allocation_id, item)

    async def inspect(self, allocation_id: str, state: dict[str, JsonValue] | None) -> LiveBrowser | None:
        await self._check_scope()
        # Kernel names and tags are mutable and can be reused. Without the
        # returned session ID, even a matching historical record is not proof
        # that the dispatched request has completed.
        if state is None:
            raise BrowserProviderError("cloud_inspect_unresolved")
        item = await self._get(allocation_id, str(state["id"]))
        if item.get("deleted_at"):
            return None
        return self._live(allocation_id, item)

    async def delete(self, allocation_id: str, state: dict[str, JsonValue] | None) -> None:
        await self._check_scope()
        if state is None:
            raise BrowserProviderError("cloud_delete_unresolved")
        item = await self._get(allocation_id, str(state["id"]))
        if not isinstance(item.get("session_id"), str):
            raise BrowserProviderError("cloud_delete_unresolved")
        session_id = item["session_id"]
        if not item.get("deleted_at"):
            await self.api.request("DELETE", f"/browsers/{quote(session_id, safe='')}", allow_missing=True)
            item = await self._get(allocation_id, session_id)
        if not item.get("deleted_at"):
            raise BrowserProviderError("cloud_delete_unresolved")

    async def connect(self, state: dict[str, JsonValue]) -> Chromium:
        return await _connect(self.api, state)

    async def close(self) -> None:
        await self.api.close()


class BrowserUse(CloudUnsupportedOperations):
    """Recover a standalone browser by exact allocation metadata."""

    def __init__(self, config: BrowserUseConfiguration, api: CloudAPI):
        self.config, self.api = config, api

    @staticmethod
    def _json(response: httpx2.Response) -> object:
        try:
            return response.json()
        except ValueError:
            raise BrowserProviderError("cloud_invalid_response") from None

    async def _check_scope(self) -> None:
        response = await self.api.request("GET", "/api/v2/billing/account")
        data = self._json(response)
        if not isinstance(data, dict) or not isinstance(data.get("projectId"), str):
            raise BrowserProviderError("cloud_invalid_response")
        if data["projectId"] != self.config.project_id:
            raise BrowserProviderError("cloud_account_mismatch", certainty="not_dispatched")

    @staticmethod
    def _owned(allocation_id: str, item: dict) -> bool:
        metadata = item.get("metadata")
        return isinstance(metadata, dict) and metadata.get("a13n_allocation") == allocation_id

    async def _find(self, allocation_id: str) -> dict | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", allocation_id):
            raise BrowserProviderError("cloud_invalid_allocation", certainty="not_dispatched")
        matches = []
        page = 1
        while True:
            response = await self.api.request(
                "GET",
                "/api/v4/browsers",
                params={"metadata": f"a13n_allocation={allocation_id}", "pageNumber": page, "pageSize": 100},
            )
            data = self._json(response)
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise BrowserProviderError("cloud_invalid_response")
            items = data["items"]
            total = data.get("totalItems")
            if (
                not isinstance(total, int)
                or isinstance(total, bool)
                or total < 0
                or data.get("pageNumber") != page
                or data.get("pageSize") != 100
                or len(items) != min(100, max(0, total - (page - 1) * 100))
                or any(not isinstance(item, dict) or not self._owned(allocation_id, item) for item in items)
            ):
                raise BrowserProviderError("cloud_invalid_response")
            matches.extend(items)
            if len(matches) > 1:
                raise BrowserProviderError("cloud_ambiguous_allocation")
            if page * 100 >= total:
                return matches[0] if matches else None
            page += 1

    async def _get(self, allocation_id: str, session_id: str) -> dict:
        response = await self.api.request("GET", f"/api/v4/browsers/{quote(session_id, safe='')}")
        item = self._json(response)
        if not isinstance(item, dict) or item.get("id") != session_id or not self._owned(allocation_id, item):
            raise BrowserProviderError("cloud_identity_mismatch")
        return item

    def _live(self, allocation_id: str, item: dict) -> LiveBrowser:
        session_id, url, expires_at = item.get("id"), item.get("cdpUrl"), item.get("timeoutAt")
        if item.get("status") != "active" or not isinstance(session_id, str) or not isinstance(url, str):
            raise BrowserProviderError("cloud_session_not_ready")
        if not isinstance(expires_at, str):
            raise BrowserProviderError("cloud_invalid_response")
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            raise BrowserProviderError("cloud_invalid_response") from None
        # Browser Use documents cdpUrl as a WebSocket endpoint, but currently returns
        # an HTTPS scheme. WebSocket upgrades use the equivalent secure scheme.
        parsed = urlsplit(url)
        if parsed.scheme == "https":
            url = parsed._replace(scheme="wss").geturl()
        return LiveBrowser(
            {"id": session_id, "allocation_id": allocation_id, "cdp_url": url}, expiry, runtime_id=session_id
        )

    async def create(self, allocation_id: str, recipe: BaseModel) -> LiveBrowser:
        if not isinstance(recipe, BrowserUseRecipe):
            raise BrowserProviderError("cloud_invalid_recipe", certainty="not_dispatched")
        try:
            await self._check_scope()
        except BrowserProviderError as exc:
            raise BrowserProviderError(exc.code, certainty="not_dispatched") from None
        response = await self.api.request(
            "POST",
            "/api/v4/browsers",
            create=True,
            body={
                "metadata": {"a13n_allocation": allocation_id},
                "timeout": recipe.timeout_minutes,
                "proxyCountryCode": None,
                "solveCaptchas": False,
                "enableRecording": False,
            },
        )
        item = self._json(response)
        if not isinstance(item, dict) or not self._owned(allocation_id, item):
            raise BrowserProviderError("cloud_identity_mismatch")
        return self._live(allocation_id, item)

    async def inspect(self, allocation_id: str, state: dict[str, JsonValue] | None) -> LiveBrowser | None:
        await self._check_scope()
        item = await self._find(allocation_id) if state is None else await self._get(allocation_id, str(state["id"]))
        if item is None:
            # An empty search does not rule out a delayed create.
            raise BrowserProviderError("cloud_inspect_unresolved")
        if state is None:
            session_id = item.get("id")
            if not isinstance(session_id, str):
                raise BrowserProviderError("cloud_invalid_response")
            item = await self._get(allocation_id, session_id)
        if item.get("status") == "stopped":
            return None
        return self._live(allocation_id, item)

    async def delete(self, allocation_id: str, state: dict[str, JsonValue] | None) -> None:
        await self._check_scope()
        item = await self._find(allocation_id) if state is None else await self._get(allocation_id, str(state["id"]))
        if item is None or not isinstance(item.get("id"), str):
            raise BrowserProviderError("cloud_delete_unresolved")
        session_id = item["id"]
        if state is None:
            item = await self._get(allocation_id, session_id)
        if item.get("status") == "active":
            await self.api.request("PATCH", f"/api/v4/browsers/{quote(session_id, safe='')}", body={"action": "stop"})
            item = await self._get(allocation_id, session_id)
        if item.get("status") != "stopped":
            raise BrowserProviderError("cloud_delete_unresolved")

    async def connect(self, state: dict[str, JsonValue]) -> Chromium:
        return await _connect(self.api, state)

    async def close(self) -> None:
        await self.api.close()
