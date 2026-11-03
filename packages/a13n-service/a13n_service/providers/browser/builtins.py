"""Built-in Browser definitions bound to operator-owned local runtime settings."""

from a13n_harness.providers.endpoint_policy import EndpointPolicy
from docker import DockerClient
from pydantic import BaseModel

from a13n_service.providers.browser import cloud, docker, native
from a13n_service.providers.browser.definition import BrowserProviderDefinition
from a13n_service.providers.browser.selfhost import Recipe, blocking
from a13n_service.settings import Browsers


def builtins(settings: Browsers, response_bytes: int = 16777216) -> tuple[BrowserProviderDefinition, ...]:
    async def open_browserbase(
        config: cloud.BrowserbaseConfiguration, credential: cloud.CloudCredential | None, policy: EndpointPolicy
    ):
        assert credential is not None
        return cloud.Browserbase(config, cloud.CloudAPI("browserbase", credential, policy, max_bytes=response_bytes))

    async def open_kernel(
        config: cloud.KernelConfiguration, credential: cloud.CloudCredential | None, policy: EndpointPolicy
    ):
        assert credential is not None
        return cloud.Kernel(config, cloud.CloudAPI("kernel", credential, policy, max_bytes=response_bytes))

    async def open_browser_use(
        config: cloud.BrowserUseConfiguration, credential: cloud.CloudCredential | None, policy: EndpointPolicy
    ):
        assert credential is not None
        return cloud.BrowserUse(config, cloud.CloudAPI("browser_use", credential, policy, max_bytes=response_bytes))

    definitions: list[BrowserProviderDefinition] = [
        BrowserProviderDefinition(
            type="browserbase",
            display_name="Browserbase",
            configuration_model=cloud.BrowserbaseConfiguration,
            credential_model=cloud.CloudCredential,
            recipe_model=cloud.BrowserbaseRecipe,
            backend_identity=lambda config: {"project_id": config.project_id},
            provider_factory=open_browserbase,
        ),
        BrowserProviderDefinition(
            type="browser_use",
            display_name="Browser Use Cloud",
            configuration_model=cloud.BrowserUseConfiguration,
            credential_model=cloud.CloudCredential,
            recipe_model=cloud.BrowserUseRecipe,
            backend_identity=lambda config: {"project_id": config.project_id},
            provider_factory=open_browser_use,
        ),
        BrowserProviderDefinition(
            type="kernel",
            display_name="Kernel",
            configuration_model=cloud.KernelConfiguration,
            credential_model=cloud.CloudCredential,
            recipe_model=cloud.KernelRecipe,
            backend_identity=lambda config: {
                "organization_id": config.organization_id,
                "project_id": config.project_id,
            },
            provider_factory=open_kernel,
        ),
    ]
    if settings.root is None:
        return tuple(definitions)
    root = settings.root

    async def open_docker(config: docker.Configuration, credential: BaseModel | None, policy: EndpointPolicy):
        client = await blocking(
            DockerClient, base_url=settings.docker_host or "unix:///var/run/docker.sock", timeout=30
        )
        return docker.Docker(root / "docker", client, config)

    async def open_native(config: native.Configuration, credential: BaseModel | None, policy: EndpointPolicy):
        return native.Native(root / "native", settings.native_chromium)

    definitions.append(
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
    )
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
