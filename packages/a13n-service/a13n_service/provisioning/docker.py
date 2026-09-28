"""Discover the operator's Docker Engine without creating containers or pulling images."""

from a13n_harness.providers.endpoint_policy import EndpointPolicy

from a13n_service.providers.registry import Registry
from a13n_service.provisioning.defaults import Defaults
from a13n_service.resources.environment_templates.schemas import TemplateConfig
from a13n_service.resources.providers.probe import probe
from a13n_service.resources.providers.schemas import ProviderCreate
from a13n_service.settings import DockerProvisioning


async def prepare(config: DockerProvisioning, registry: Registry, policy: EndpointPolicy) -> Defaults:
    result = await probe(
        registry,
        "environment",
        "docker",
        config={},
        credential=None,
        extra_headers={},
        policy=policy,
        timeout=3,
        max_bytes=4096,
    )
    if result.status != "succeeded":
        raise RuntimeError(result.message or "Docker Engine is unavailable")
    return Defaults(
        ProviderCreate(type="docker", name="Docker", config={}),
        "Linux Sandbox",
        TemplateConfig(recipe={"image": config.image, "pull_policy": config.pull_policy}),
    )
