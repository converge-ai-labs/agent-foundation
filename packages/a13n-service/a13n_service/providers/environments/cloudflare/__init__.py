"""Service lifecycle for a Cloudflare Containers gateway."""

from datetime import datetime
from typing import Literal

import httpx2
from a13n_harness.environment.errors import EnvironmentProviderError, provider_error
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from pydantic import BaseModel, SecretStr

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.outbound import open_http
from a13n_service.providers.environment._backend import ManagementBackend
from a13n_service.providers.environment._backend_factory import ManagementBackendFactory
from a13n_service.providers.environment.cloudflare import Account
from a13n_service.providers.environment.definition import management_definition
from a13n_service.providers.environment.envd import envd_environment
from a13n_service.providers.environment.management import Pending

from .execution import EXECUTION, KEY, Gateway, Recipe, Target, gateway, identity

PATH = "/v1/environment"


class Observation(BaseModel):
    status: Literal["running", "pending", "absent"]


class Management(ManagementBackend):
    @property
    def provider_key(self) -> str:
        return KEY

    def __init__(
        self,
        *,
        configuration: Recipe,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: Gateway | None,
        operation_id: str,
    ) -> None:
        super().__init__(state)
        self.configuration = configuration
        self._environment_id = environment_id
        if runtime is None:
            raise TypeError("Cloudflare requires a gateway")
        self.runtime = runtime
        self.operation_id = operation_id

    @property
    def environment_id(self) -> str:
        return self._environment_id

    def validate_management(self) -> None:
        if (
            self.state is not None
            and identity(configuration=self.configuration, state=self.state) != self.environment_id
        ):
            raise provider_error(KEY, "provider_state_mismatch", Category.CONFLICT)

    async def request(self, method: str, path: str = PATH, *, payload: object = None) -> httpx2.Response:
        try:
            async with open_http(self.runtime.account._endpoint_policy, timeout=15, max_bytes=8192) as client:
                response = await client.request(
                    method,
                    self.runtime.account.endpoint + path,
                    headers={
                        "Authorization": "Bearer " + self.runtime.secret.get_secret_value(),
                        **self.runtime.route(self.environment_id),
                    },
                    json=payload,
                )
        except (httpx2.HTTPError, ServiceError):
            raise provider_error(
                KEY,
                "cloudflare_gateway_unavailable",
                Category.UNAVAILABLE,
                certainty=Certainty.UNKNOWN if method != "GET" else Certainty.KNOWN,
            ) from None
        if response.status_code == 409:
            raise provider_error(KEY, "provider_target_changed", Category.CONFLICT)
        if response.status_code == 400:
            raise provider_error(KEY, "cloudflare_gateway_rejected_configuration", Category.INVALID)
        if response.status_code == 410:
            raise self.lost()
        if not response.is_success:
            raise provider_error(
                KEY,
                "cloudflare_gateway_unavailable",
                Category.UNAVAILABLE,
                certainty=Certainty.UNKNOWN if method != "GET" else Certainty.KNOWN,
            )
        return response

    @staticmethod
    def lost() -> Exception:
        return provider_error(KEY, "environment_lost", Category.MISSING, certainty=Certainty.KNOWN)

    def observation(self, response: httpx2.Response) -> Observation:
        try:
            return Observation.model_validate_json(response.content)
        except ValueError:
            raise provider_error(
                KEY, "cloudflare_gateway_invalid_response", Category.UNAVAILABLE, certainty=Certainty.UNKNOWN
            ) from None

    async def create(self) -> Pending | None:
        if self.state is None:
            self._cache_state(
                EnvironmentState(
                    provider_key=KEY,
                    state_version="1",
                    state=Target(environment_id=self.environment_id, execution=self.configuration.execution).model_dump(
                        mode="json"
                    ),
                )
            )
            return Pending(self.state)
        spec = {
            "operation": self.operation_id,
            "mode": self.configuration.execution,
            "network_egress": self.configuration.network_egress,
        }
        spec.update(envd_environment({}, self.configuration.envd))
        outbound = self.websocket
        payload = (
            dict(spec)
            if outbound is None
            else {
                **spec,
                "outbound": {"url": outbound.url, "token": outbound.credential.get_secret_value()},
            }
        )
        observation = self.observation(await self.request("PUT", payload=payload))
        if observation.status == "absent":
            raise self.lost()
        if observation.status == "pending":
            return Pending(self.state, settled=True)
        if outbound is None:
            try:
                async with self.runtime.session(self.environment_id):
                    pass
            except EnvironmentProviderError as error:
                if error.category not in {Category.UNAVAILABLE, Category.TIMEOUT}:
                    raise
                return Pending(self.state, settled=True)
        return None

    async def inspect(self) -> Literal["running", "stopped", "absent"]:
        status = self.observation(await self.request("GET")).status
        if status == "pending":
            raise provider_error(KEY, "cloudflare_container_starting", Category.UNAVAILABLE)
        return status

    async def destroy(self) -> None:
        await self.request("DELETE")

    async def keepalive(self, *, deadline: datetime, operation_id: str) -> datetime:
        response = await self.request("POST", PATH + "/renew")
        try:
            expires_at = datetime.fromisoformat(response.json()["expires_at"])
            if expires_at.tzinfo is None:
                raise ValueError("Missing time zone")
        except (KeyError, TypeError, ValueError):
            raise provider_error(KEY, "cloudflare_gateway_invalid_response", Category.UNAVAILABLE) from None
        return expires_at


def smoke_gateway(endpoint: str, environment_id: str, secret: SecretStr, policy: EndpointPolicy) -> ManagementBackend:
    account = Account(endpoint=endpoint)
    account._endpoint_policy = policy
    return Management(
        configuration=Recipe(network_egress="disabled"),
        environment_id=environment_id,
        state=None,
        runtime=Gateway(account, secret),
        operation_id=environment_id,
    )


_factory = ManagementBackendFactory(key=KEY, environment_model=Recipe, management=Management, runtime_factory=gateway)
CLOUDFLARE = management_definition(
    EXECUTION,
    environment_model=Recipe,
    provider_factory=_factory.provider,
    supports_destroy=True,
    requires_keepalive=True,
)
