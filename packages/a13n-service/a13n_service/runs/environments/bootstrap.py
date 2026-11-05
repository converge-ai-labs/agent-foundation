"""Instance-private Envd credentials, frozen before allocation and kept until deletion."""

import secrets

from pydantic import SecretStr

from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation
from a13n_service.runs.environments.tables import EnvironmentRow


def location(environment: EnvironmentRow) -> SecretLocation:
    return SecretLocation(environment.organization_id, environment.__tablename__, "envd_bootstrap", environment.id)


def freeze(keys: KeyRing, environment: EnvironmentRow) -> None:
    """The caller holds the instance lock; no native allocation precedes this commit."""
    if environment.envd_bootstrap is None:
        environment.envd_bootstrap = keys.protect(
            secrets.token_urlsafe(32).encode(), location(environment)
        ).model_dump()


def restore(keys: KeyRing, environment: EnvironmentRow) -> SecretStr | None:
    if environment.envd_bootstrap is None:
        return None
    return SecretStr(keys.reveal(Envelope.model_validate(environment.envd_bootstrap), location(environment)).decode())
