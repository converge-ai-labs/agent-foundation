"""Private sampler/exporter launch inputs and session-free cached Device observations."""

from dataclasses import dataclass, field

from a13n_envd_client.eip.v1 import DeviceDescriptor, DeviceMetricsResult
from a13n_harness.providers.environment.remote_envd.connections import WebSocketEnvdConnections
from a13n_harness.providers.environment.remote_envd.http import HttpEnvdProviderRuntime
from pydantic import BaseModel, SecretStr


class DeviceObservation(BaseModel):
    descriptor: DeviceDescriptor
    metrics: DeviceMetricsResult | None


async def observe(runtime: HttpEnvdProviderRuntime | WebSocketEnvdConnections, device_id: str) -> DeviceObservation:
    device = await runtime.acquire_device(expected_device_id=device_id)
    descriptor = device.descriptor
    metrics = await device.metrics() if "device.metrics" in descriptor.available_methods else None
    return DeviceObservation(descriptor=descriptor, metrics=metrics)


@dataclass(frozen=True, slots=True)
class ScrapeTarget:
    """Private HTTP route and listener credential; safe to deliver only to trusted collectors."""

    endpoint: str
    token: SecretStr = field(repr=False)
    verify_tls: bool = True
    headers: dict[str, SecretStr] = field(default_factory=dict, repr=False)


def scrape_target(runtime: HttpEnvdProviderRuntime) -> ScrapeTarget:
    # Managed carriers currently use the operator's boolean TLS policy. An
    # arbitrary SSLContext (especially mTLS) cannot be reproduced in another process.
    if not isinstance(runtime.verify, bool):
        raise NotImplementedError("Collector targets require a portable TLS policy")
    return ScrapeTarget(
        runtime.configuration.endpoint, runtime.credential.token, runtime.verify, dict(runtime.credential.headers)
    )


@dataclass(frozen=True, slots=True)
class MetricsLaunch:
    # Prepared by the Host, including write credentials. Never part of a recipe,
    # portable provider state, target fingerprint, or diagnostic representation.
    environment: dict[str, str] = field(repr=False)
    sample_interval_ms: int = 10_000
