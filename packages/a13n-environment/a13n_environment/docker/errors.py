"""Safe Engine failure classification; transport failure never proves absence."""

from collections.abc import Iterator
from contextlib import contextmanager

from ..errors import EnvironmentProviderError, EnvironmentProviderErrorContext
from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import EnvironmentProviderOutcomeCertainty as Certainty
from ..errors import EnvironmentProviderRecoveryHint as Recovery


@contextmanager
def engine_errors(*, mutation: bool = False) -> Iterator[None]:
    from docker.errors import DockerException

    try:
        yield
    except (DockerException, OSError):
        raise EnvironmentProviderError(
            "Docker Engine request failed",
            code="provider_unknown_outcome" if mutation else "provider_unavailable",
            category=Category.UNKNOWN_OUTCOME if mutation else Category.UNAVAILABLE,
            certainty=Certainty.UNKNOWN if mutation else Certainty.NOT_DISPATCHED,
            recovery_hint=Recovery.RECONCILE if mutation else Recovery.REFRESH_RUNTIME,
            context=EnvironmentProviderErrorContext(provider_key="a13n.docker"),
        ) from None
