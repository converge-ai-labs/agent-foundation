"""External envd targets: how one is reached, verified and its token kept.

Verification runs outside any transaction, within `providers.operation_seconds`: at registration first contact reads
the device identity the daemon states, then one session opens and closes with that identity expected. The row keeps
only the endpoint, the device ID and the token, encrypted for the row; every later connection expects the same device.
"""

import anyio
from a13n_environment.errors import EnvironmentProviderError
from a13n_environment.models import EnvironmentError as OperationError

from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation
from a13n_service.infra.errors import ServiceError, conflict
from a13n_service.providers import envd
from a13n_service.runs.environments.adapters import ExternalAccount
from a13n_service.runs.environments.lifecycle import PERMANENT
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime


def _location(organization_id: str, environment_id: str) -> SecretLocation:
    return SecretLocation(organization_id, "environments", "token", environment_id)


def seal(keys: KeyRing, environment: EnvironmentRow, token: str) -> dict:
    """The token's envelope, bound to the environment's row and column."""
    location = _location(environment.organization_id, environment.id)
    return keys.protect(token.encode(), location).model_dump(mode="json")


def account(environment: EnvironmentRow) -> ExternalAccount:
    """How to reach a live external target."""
    endpoint, device_id, token = environment.endpoint, environment.device_id, environment.token
    assert endpoint is not None and device_id is not None and token is not None, "a live external target"
    location = _location(environment.organization_id, environment.id)
    return ExternalAccount(endpoint, device_id, Envelope.model_validate(token), location)


async def verify(runtime: Runtime, environment_id: str, endpoint: str, token: str, *, device_id: str | None) -> str:
    """The device the daemon at `endpoint` is, proved with one session: `device_id` when the target already has
    one (`provider_device_mismatch` when the endpoint now reaches another), else the identity it states.

    A refusal that repeating cannot overcome is `conflict`, on the environment or, at registration, on
    `endpoint`, with the provider's code as reason; anything else is the target being unavailable for now.
    """
    try:
        with anyio.fail_after(runtime.settings.providers.operation_seconds):
            identity = device_id or await envd.first_contact(endpoint, token, runtime.endpoint_policy)
            adapter = await envd.connect(
                endpoint, token, environment_id=environment_id, device_id=identity, policy=runtime.endpoint_policy
            )
            async with await adapter.open():
                pass
            return identity
    except EnvironmentProviderError as error:
        if error.category not in PERMANENT:
            raise _unverified(error.code) from None
        if device_id is None:
            reason = {"field": "endpoint", "reason": error.code}
            raise ServiceError("conflict", f"endpoint: {error.code}", reason) from None
        raise conflict("environment", environment_id, error.code) from None
    except (OperationError, TimeoutError) as error:
        raise _unverified("environment_timeout" if isinstance(error, TimeoutError) else error.code) from None


def _unverified(reason: str) -> ServiceError:
    return ServiceError(
        "unavailable",
        "The external target could not be verified",
        {"dependency": f"environment:{envd.TYPE}", "reason": reason},
    )
