"""Detached Host inputs for explicit management and fixed-target execution."""

from collections.abc import Mapping
from dataclasses import dataclass, field

import anyio
from a13n_environment.execution import EnvironmentConnector, EnvironmentExecution
from a13n_environment.management import EnvironmentProvider
from a13n_environment.models import EnvironmentState
from a13n_harness import RunConfiguration
from a13n_logging import exception_details, get_logger
from pydantic import JsonValue

from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation
from a13n_service.providers import envd
from a13n_service.providers.registry import Registry
from a13n_service.resources.providers.service import ResolvedProvider
from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)
_CLOSE_SECONDS = 10


@dataclass(frozen=True, slots=True)
class ExternalAccount:
    """An external target's endpoint, the device it must reach and its token, sealed for the row that stores it."""

    endpoint: str
    device_id: str
    token: Envelope = field(repr=False)
    location: SecretLocation = field(repr=False)

    def reveal_token(self, keys: KeyRing) -> str:
        return keys.reveal(self.token, self.location).decode()


@dataclass(frozen=True, slots=True)
class Target:
    """What building an adapter for one instance needs, detached from the database: the provider account of a
    managed instance, or an external target's own."""

    environment_id: str
    account: ResolvedProvider | ExternalAccount = field(repr=False)
    recipe: Mapping[str, JsonValue]
    state: EnvironmentState | None

    def __post_init__(self) -> None:
        # Old persisted handles omitted the then-fixed image, even for interrupted creates.
        # Resolve that stored meaning once for both management and execution; new handles pin defaults.
        if isinstance(self.account, ResolvedProvider) and self.account.type == "docker" and "image" not in self.recipe:
            object.__setattr__(
                self, "recipe", {**self.recipe, "image": "ghcr.io/converge-ai-labs/a13n-docker-environment:dev"}
            )


async def open_provider(runtime: Runtime, target: Target) -> EnvironmentProvider:
    """Acquire account management clients after leaving the database transaction."""
    account = target.account
    if isinstance(account, ExternalAccount):
        raise ValueError("External targets have no Service management authority")
    await runtime.registry.check_environment_endpoint(account.type, account.config, runtime.endpoint_policy)
    return await runtime.registry.get("environment", account.type).open_provider(
        configuration=account.config,
        credential=account.reveal_credential(runtime.keys),
    )


async def execution_connector(
    runtime: Runtime, target: Target, *, configuration: RunConfiguration | None = None
) -> EnvironmentConnector:
    """Check Host endpoint policy, then construct an inert connector to the published target."""
    policy = runtime.endpoint_policy.for_run(configuration or RunConfiguration())
    account = target.account
    if isinstance(account, ExternalAccount):
        return await envd.connect(
            account.endpoint,
            account.reveal_token(runtime.keys),
            environment_id=target.environment_id,
            device_id=account.device_id,
            policy=policy,
        )
    await runtime.registry.check_environment_endpoint(account.type, account.config, policy)
    return runtime.registry.get("environment", account.type).execution_connector(
        dict(target.recipe),
        configuration=account.config,
        credential=account.reveal_credential(runtime.keys),
        environment_id=target.environment_id,
        state=target.state,
    )


async def close(resource: EnvironmentProvider | EnvironmentExecution) -> None:
    """Release owned local clients within the Host cleanup deadline."""
    with anyio.CancelScope(shield=True), anyio.move_on_after(_CLOSE_SECONDS):
        try:
            await resource.close()
        except Exception as error:
            logger.warning(
                "Environment resource close failed",
                extra={"error_type": type(error).__name__, "exception_details": exception_details(error)},
            )


def credential_version(provider: ResolvedProvider) -> str | None:
    """What changes whenever the provider's credential is written, and reveals nothing of it: its envelope's nonce."""
    return None if provider.credential is None else provider.credential.nonce


def provider_identity(registry: Registry, provider: ResolvedProvider) -> dict[str, JsonValue]:
    """The provider type and non-secret backend locator a managed instance's handle is meaningful in."""
    definition = registry.get("environment", provider.type)
    return {
        "type": provider.type,
        "backend": definition.backend_identity(definition.configuration_model.model_validate(provider.config)),
    }
