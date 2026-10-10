"""External envd targets: a daemon someone runs and registers by its HTTP(S) endpoint and token.

The Harness HTTP envd definition is their adapter, not a provider type anyone configures. Every dial first checks
the endpoint against the outbound policy. First contact reads the device identity the daemon states; every later
connection expects that identity, so an endpoint that now reaches another daemon is refused.
"""

from a13n_environment.execution import EnvironmentConnector
from a13n_environment.models import EnvironmentState
from a13n_environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
    RemoteEnvdStateData,
)
from a13n_environment.remote_envd.http import HTTP_ENVD, HttpEnvdProviderRuntime
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from pydantic import BaseModel, SecretStr, ValidationError

from a13n_service.providers.endpoints import check_endpoint

TYPE = HTTP_ENVD.type


def _checked[M: BaseModel](value: str, model: type[M], field: str) -> M:
    try:
        return model.model_validate({field: value})
    except ValidationError as error:
        # The validator's own words, never the input, which can be the token.
        raise ValueError(error.errors(include_input=False, include_url=False)[0]["msg"]) from None


def normalized_endpoint(value: str) -> str:
    """The canonical origin of a daemon's endpoint: HTTPS, or plain HTTP to loopback only; no path or query."""
    return _checked(value, HttpEnvdConnectionConfiguration, "endpoint").endpoint


def checked_token(value: str) -> str:
    """A token the daemon's credential rules accept: bounded, without whitespace or control characters."""
    _checked(value, HttpEnvdCredential, "token")
    return value


async def first_contact(endpoint: str, token: str, policy: EndpointPolicy) -> str:
    """The device identity the daemon at `endpoint` states when it accepts `token`; no session opens."""
    await check_endpoint(TYPE, endpoint, policy)
    configuration = HttpEnvdConnectionConfiguration(endpoint=endpoint)
    async with HttpEnvdProviderRuntime(configuration, HttpEnvdCredential(token=SecretStr(token))) as runtime:
        return (await runtime.describe(expected_device_id=None)).device_id


async def connect(
    endpoint: str, token: str, *, environment_id: str, device_id: str, policy: EndpointPolicy
) -> EnvironmentConnector:
    """A fresh, unentered adapter that expects `device_id`, refused as `provider_device_mismatch` by another
    device; it never creates or changes anything there."""
    await check_endpoint(TYPE, endpoint, policy)
    state = RemoteEnvdStateData(device_id=device_id).model_dump()
    return HTTP_ENVD.execution_connector(
        {},
        configuration={"endpoint": endpoint},
        credential={"token": token},
        environment_id=environment_id,
        state=EnvironmentState(provider_key=TYPE, state_version="1", state=state),
    )
