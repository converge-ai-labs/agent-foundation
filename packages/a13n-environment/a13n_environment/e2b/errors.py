"""Bounded E2B errors; upstream exception text never becomes a public error."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from ..errors import EnvironmentProviderError
from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import EnvironmentProviderOutcomeCertainty as Certainty
from ..errors import EnvironmentProviderRecoveryHint as Recovery
from ..errors import provider_error as typed_provider_error
from ..models import EnvironmentError
from .configuration import PROVIDER_KEY


def provider_error(code: str, category: Category, *, uncertain: bool = False) -> EnvironmentProviderError:
    """An uncertain mutation must be reconciled; everything else is a caller fix."""
    return typed_provider_error(
        PROVIDER_KEY,
        code,
        category,
        certainty=Certainty.UNKNOWN if uncertain else Certainty.NOT_DISPATCHED,
        recovery_hint=Recovery.RECONCILE if uncertain else Recovery.FIX_INPUT,
        description="E2B environment operation failed.",
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
