"""Bounded E2B errors; upstream exception text never becomes a public error."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from ..errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorContext,
)
from ..errors import (
    EnvironmentProviderErrorCategory as Category,
)
from ..errors import (
    EnvironmentProviderOutcomeCertainty as Certainty,
)
from ..errors import (
    EnvironmentProviderRecoveryHint as Recovery,
)
from ..models import EnvironmentError
from .configuration import PROVIDER_KEY


def provider_error(code: str, category: Category, *, uncertain: bool = False) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        "E2B environment operation failed.",
        code=code,
        category=category,
        certainty=Certainty.UNKNOWN if uncertain else Certainty.NOT_DISPATCHED,
        recovery_hint=Recovery.RECONCILE if uncertain else Recovery.FIX_INPUT,
        context=EnvironmentProviderErrorContext(provider_key=PROVIDER_KEY),
    )


@contextmanager
def sdk_errors(*, mutation: bool = False) -> Iterator[None]:
    from e2b.exceptions import AuthenticationException, InvalidArgumentException, RateLimitException, TemplateException

    try:
        yield
    except (EnvironmentError, EnvironmentProviderError):
        raise
    except AuthenticationException:
        raise provider_error("provider_denied", Category.DENIED) from None
    except InvalidArgumentException:
        raise provider_error("provider_spec_invalid", Category.INVALID) from None
    except TemplateException:
        raise provider_error("provider_template_unsupported", Category.UNSUPPORTED) from None
    except RateLimitException:
        raise provider_error("provider_unavailable", Category.UNAVAILABLE) from None
    except Exception:
        raise provider_error(
            "provider_unknown_outcome" if mutation else "provider_unavailable",
            Category.UNKNOWN_OUTCOME if mutation else Category.UNAVAILABLE,
            uncertain=mutation,
        ) from None
