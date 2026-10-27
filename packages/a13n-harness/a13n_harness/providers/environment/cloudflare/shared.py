"""Cloudflare target identity and authenticated bridge access; no provisioning."""

import hashlib
import hmac
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.environment.variables import EnvironmentVariables
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.environment.native.configuration import TargetState, TokenCredential
from a13n_harness.providers.environment.native.environment import NativeReference, NativeRuntime
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.http import NativeHTTP, decode_response

REVISION = "v1"


class CloudflareConnectionConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    _endpoint_policy: EndpointPolicy = PrivateAttr(default_factory=EndpointPolicy)
    account_id: str = Field(pattern=r"^[0-9a-f]{32}$", title="Cloudflare account ID")


class CloudflareEnvironmentConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    instance: Literal["lite", "standard-1", "standard-2", "standard-3", "standard-4"] = "standard-1"
    environment: EnvironmentVariables = Field(default_factory=dict)
    request_timeout_seconds: float = Field(default=120, gt=0, le=600, allow_inf_nan=False)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class CloudflareState(TargetState):
    lifecycle_version: int = Field(default=0, ge=0)
    account_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    deployment: str = Field(pattern=r"^a13n-env-[0-9a-f]{32}-v[0-9]+$")
    endpoint: str = Field(pattern=r"^https://a13n-env-[0-9a-f]{32}-v[0-9]+\.[a-z0-9-]+\.workers\.dev$")


class Target(BaseModel):
    version: int = Field(default=0, ge=0)
    environment_id: str
    fingerprint: str
    status: Literal["running", "stopped", "absent", "starting", "stopping", "lost"]
    snapshot_id: str | None = None
    snapshot_expires_at: str | None = None


def deployment(account: str, token: str) -> str:
    scope = hmac.new(token.encode(), f"a13n-cloudflare:{account}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"a13n-env-{scope}-{REVISION}"


def bridge_token(script: str, token: str) -> str:
    return hmac.new(token.encode(), f"a13n-bridge:{script}".encode(), hashlib.sha256).hexdigest()


class CloudflareReference(NativeReference[CloudflareEnvironmentConfiguration, CloudflareState]):
    def __init__(
        self,
        config: CloudflareEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
    ):
        super().__init__("cloudflare", config, environment_id, state, state_model=CloudflareState)
        assert isinstance(runtime.configuration, CloudflareConnectionConfiguration)
        assert isinstance(runtime.credential, TokenCredential)
        self.backend = runtime.configuration
        self.credential = runtime.credential
        self.script = deployment(self.backend.account_id, self.credential.api_key.get_secret_value())
        self.http: NativeHTTP | None = None
        if self.target is not None and (
            self.target.account_id != self.backend.account_id
            or self.target.deployment != self.script
            or not self.target.endpoint.startswith("https://" + self.script + ".")
        ):
            raise failure("cloudflare", "provider_account_or_credential_changed", Category.CONFLICT)

    async def transport(self) -> NativeHTTP:
        self.require_target()
        assert self.target is not None
        if self.http is None:
            # State is not authority to send deployment credentials to another account's workers.dev host.
            api = NativeHTTP(
                "cloudflare",
                f"https://api.cloudflare.com/client/v4/accounts/{self.backend.account_id}/",
                self.credential.api_key.get_secret_value(),
                self.config.request_timeout_seconds,
            )
            try:
                raw = await api.request("GET", "workers/subdomain")
            finally:
                await api.close()
            result = raw.get("result") if isinstance(raw, dict) and raw.get("success") is True else None
            suffix = result.get("subdomain") if isinstance(result, dict) else None
            if not isinstance(suffix, str) or self.target.endpoint != f"https://{self.script}.{suffix}.workers.dev":
                raise failure("cloudflare", "provider_endpoint_conflict", Category.CONFLICT)
            await self.backend._endpoint_policy.validate(self.target.endpoint)
            self.http = NativeHTTP(
                "cloudflare",
                self.target.endpoint + "/",
                bridge_token(self.script, self.credential.api_key.get_secret_value()),
                self.config.request_timeout_seconds,
            )
        return self.http

    async def lookup(self) -> Target:
        self.require_target()
        assert self.target is not None
        raw = await (await self.transport()).request(
            "GET",
            "control/inspect",
            headers={"X-A13N-Environment": self.owner, "X-A13N-Fingerprint": self.config.fingerprint},
        )
        target = decode_response(Target, raw, "cloudflare")
        if target.environment_id != self.owner or target.fingerprint != self.config.fingerprint:
            raise failure("cloudflare", "provider_target_conflict", Category.CONFLICT)
        return target

    async def status(self) -> Literal["running", "stopped", "absent"]:
        status = (await self.lookup()).status
        if status != "running" and status != "stopped" and status != "absent":
            raise failure("cloudflare", "provider_target_unavailable", Category.UNAVAILABLE)
        return status

    async def close_transport(self) -> None:
        if self.http is not None:
            await self.http.close()
            self.http = None
