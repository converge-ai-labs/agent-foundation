"""Safe Engine failure classification; transport failure never proves absence."""

from collections.abc import Iterator
from contextlib import contextmanager

from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import EnvironmentProviderOutcomeCertainty as Certainty
from ..errors import provider_error


@contextmanager
def engine_errors(*, mutation: bool = False) -> Iterator[None]:
    from docker.errors import DockerException

    try:
        yield
    except (DockerException, OSError):
        raise provider_error(
            "docker",
            "provider_unknown_outcome" if mutation else "provider_unavailable",
            Category.UNKNOWN_OUTCOME if mutation else Category.UNAVAILABLE,
            certainty=Certainty.UNKNOWN if mutation else Certainty.NOT_DISPATCHED,
            description="Docker Engine request failed",
        ) from None
