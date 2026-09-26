"""Fresh Harness adapters for one environment instance, built from plain values after the session closed.

A managed instance's registry definition is its provider contract: `create()` builds a single-use adapter from
the provider account, the instance's recipe and its portable state. An external target's adapter is the Harness
HTTP envd one, built from the target's own endpoint and token. Lifecycle operations use an adapter once and close
it; execution hands it to the Harness, which enters and closes it.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

import anyio
from a13n_harness.providers.environment.management import Environment
from a13n_harness.providers.environment.models import EnvironmentState
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


async def construct(runtime: Runtime, target: Target, *, operation_id: str | None, allow_create: bool) -> Environment:
    """A fresh, unentered adapter. `allow_create=False` connects to the existing instance and never creates,
    starts or replaces one; an external target is only ever connected to."""
    account = target.account
    if isinstance(account, ExternalAccount):
        return await envd.connect(
            account.endpoint,
            account.reveal_token(runtime.keys),
            environment_id=target.environment_id,
            device_id=account.device_id,
            policy=runtime.endpoint_policy,
        )
    registry = runtime.registry
    await registry.check_environment_endpoint(account.type, account.config, runtime.endpoint_policy)
    return await registry.get("environment", account.type).create(
        dict(target.recipe),
        configuration=account.config,
        credential=account.reveal_credential(runtime.keys),
        environment_id=target.environment_id,
        state=target.state,
        operation_id=operation_id,
        allow_create=allow_create,
    )


async def close(adapter: Environment) -> None:
    """Release the adapter's local resources, boundedly and even when cancelled; the instance itself stays."""
    with anyio.CancelScope(shield=True), anyio.move_on_after(_CLOSE_SECONDS):
        try:
            await adapter.close()
        except Exception as error:
            logger.warning(
                "Environment adapter close failed",
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
