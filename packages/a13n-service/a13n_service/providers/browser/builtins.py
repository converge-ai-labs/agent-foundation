"""Built-in Browser definitions bound to operator-owned local runtime settings."""

from a13n_harness.providers.endpoint_policy import EndpointPolicy
from docker import DockerClient
from pydantic import BaseModel

from a13n_service.providers.browser import docker, native
from a13n_service.providers.browser.definition import BrowserProviderDefinition
from a13n_service.providers.browser.selfhost import Recipe, blocking
from a13n_service.settings import Browsers


def builtins(settings: Browsers) -> tuple[BrowserProviderDefinition, ...]:
    if settings.root is None:
        return ()
    root = settings.root

    async def open_docker(config: docker.Configuration, credential: BaseModel | None, policy: EndpointPolicy):
        client = await blocking(
            DockerClient, base_url=settings.docker_host or "unix:///var/run/docker.sock", timeout=30
        )
        return docker.Docker(root / "docker", client, config)

    async def open_native(config: native.Configuration, credential: BaseModel | None, policy: EndpointPolicy):
        return native.Native(root / "native", settings.native_chromium)

    definitions: list[BrowserProviderDefinition] = [
        BrowserProviderDefinition(
            type="docker",
            display_name="Docker browser",
            configuration_model=docker.Configuration,
            recipe_model=Recipe,
            backend_identity=lambda config: {"root": str(root), "engine": settings.docker_host},
            provider_factory=open_docker,
            supports_stop=True,
            supports_files=True,
            supports_viewer=True,
        )
    ]
    if settings.native_enabled:
        definitions.append(
            BrowserProviderDefinition(
                type="native",
                display_name="Native browser (Linux debugging)",
                configuration_model=native.Configuration,
                recipe_model=Recipe,
                backend_identity=lambda config: {"root": str(root), "chromium": settings.native_chromium},
                provider_factory=open_native,
                supports_stop=True,
                supports_files=True,
                supports_viewer=True,
            )
        )
    return tuple(definitions)
