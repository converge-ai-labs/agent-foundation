"""Non-billable provider probes: a resource test checks reachability and authentication, never paid work.

A definition without such a probe is reported as unsupported; billable verification goes through a run. An
environment probe only reads from the backend its account names: it creates, starts or changes nothing there, and
an endpoint the account names passes the outbound policy before it is dialed. A memory probe lists one page of a
namespace no memory uses.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import httpx2
from a13n_environment.definition import EnvironmentProviderDefinition
from a13n_environment.docker.errors import engine_errors
from a13n_environment.docker.provider import DOCKER, DockerConnectionConfiguration
from a13n_environment.docker.runtime import DockerSDKEngine
from a13n_environment.errors import EnvironmentProviderError, EnvironmentProviderErrorCategory
from a13n_environment.errors import provider_error as environment_error
from a13n_harness.providers.connector.contracts import ConnectorProviderError
from a13n_harness.providers.connector.definition import ConnectorProviderDefinition
from a13n_harness.providers.connector.http import ConnectorHttpClient
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.memory import MemoryProviderDefinition, MemoryStoreError
from a13n_harness.providers.model.definition import ModelProviderDefinition, ProviderOperationError
from anyio import fail_after, to_thread
from pydantic import JsonValue

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.outbound import open_http
from a13n_service.providers.registry import ProviderKind, RegisteredProvider, Registry

type ProbeStatus = Literal["succeeded", "failed", "unsupported"]

# The environment types whose account a read alone can check.
_ENVIRONMENT_PROBES = frozenset({DOCKER.type})
# Default memory namespaces are `a13n-` and a hash, so the probe's never holds a memory's records.
PROBE_NAMESPACE = "a13n-probe"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    status: ProbeStatus
    # Safe to show: fixed text or a provider's classified code, never upstream bodies or inputs.
    message: str | None = None


def _engine_unavailable() -> EnvironmentProviderError:
    return environment_error(DOCKER.type, "provider_unavailable", EnvironmentProviderErrorCategory.UNAVAILABLE)


async def _ping_engine(
    definition: EnvironmentProviderDefinition,
    config: Mapping[str, JsonValue],
    endpoint: str | None,
    *,
    policy: EndpointPolicy,
    timeout: float,
    max_bytes: int,
) -> None:
    """Docker: the engine the account names answers a ping.

    A remote engine a tenant named is pinged through the Service's own client, whose policy checks every address
    it connects to and which leaves nothing behind when the probe times out. The operator's engine is pinged
    through the Docker client, whose own timeouts end its thread by the probe's deadline.
    """
    if endpoint is not None:
        try:
            async with open_http(policy, timeout=timeout, max_bytes=max_bytes) as client:
                answer = await client.get(f"{endpoint}/_ping")
        except (httpx2.HTTPError, ServiceError):
            raise _engine_unavailable() from None
        if answer.status_code != 200:
            raise _engine_unavailable()
        return
    configuration = definition.configuration_model.model_validate(config)
    if not isinstance(configuration, DockerConnectionConfiguration):
        raise TypeError("Docker accounts are Docker connection configurations")
    host, seconds = configuration.docker_host, math.ceil(timeout)

    def ping() -> bool:
        engine = DockerSDKEngine.connect(host, timeout_seconds=seconds)
        try:
            return engine.client.ping()
        finally:
            engine.client.close()

    with engine_errors():
        if not await to_thread.run_sync(ping, abandon_on_cancel=True):
            raise _engine_unavailable()


def supports_probe(definition: RegisteredProvider) -> bool:
    if isinstance(definition, ModelProviderDefinition):
        return definition.supports_connection_probe
    if isinstance(definition, EnvironmentProviderDefinition):
        return definition.type in _ENVIRONMENT_PROBES
    return isinstance(definition, ConnectorProviderDefinition | MemoryProviderDefinition)


async def probe(
    registry: Registry,
    kind: ProviderKind,
    type_: str,
    *,
    config: Mapping[str, JsonValue],
    credential: JsonValue,
    extra_headers: Mapping[str, str],
    policy: EndpointPolicy,
    timeout: float,
    max_bytes: int,
) -> ProbeResult:
    definition = registry.get(kind, type_)
    if not supports_probe(definition):
        return ProbeResult("unsupported", "This provider type has no non-billable test; verify it through a run")
    try:
        with fail_after(timeout):
            if isinstance(definition, EnvironmentProviderDefinition):
                await registry.check_environment_endpoint(type_, config, policy)
                endpoint = registry.environment_endpoint(type_, config)
                await _ping_engine(definition, config, endpoint, policy=policy, timeout=timeout, max_bytes=max_bytes)
            else:
                async with open_http(policy, timeout=timeout, max_bytes=max_bytes) as client:
                    if isinstance(definition, ModelProviderDefinition):
                        connection = definition.bind(config, credential, extra_headers=extra_headers)
                        await definition.probe(connection, http_client=client, endpoint_policy=policy)
                    elif isinstance(definition, ConnectorProviderDefinition):
                        http = ConnectorHttpClient(
                            client, policy, response_max_bytes=max_bytes, timeout_seconds=timeout
                        )
                        async with definition.open(config, credential, http=http) as runtime:
                            await runtime.test()
                    elif isinstance(definition, MemoryProviderDefinition):
                        async with definition.open(config, credential, namespace=PROBE_NAMESPACE, http=client) as store:
                            await store.list(limit=1)
    except ProviderOperationError as error:
        return ProbeResult("failed", str(error))
    except (ConnectorProviderError, EnvironmentProviderError, MemoryStoreError) as error:
        return ProbeResult("failed", error.code)
    except TimeoutError:
        return ProbeResult("failed", "Provider test timed out")
    except ValueError:
        # Validation messages can echo inputs, including credentials; endpoint denials are ValueErrors too.
        return ProbeResult("failed", "Configuration, credential or endpoint was rejected")
    return ProbeResult("succeeded")
