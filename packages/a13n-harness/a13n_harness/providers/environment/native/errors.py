"""Safe native transport errors with explicit uncertain-mutation outcomes."""

from ..errors import EnvironmentProviderError, provider_error
from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import EnvironmentProviderOutcomeCertainty as Certainty
from ..errors import EnvironmentProviderRecoveryHint as Recovery


def failure(
    key: str, code: str, category: Category, *, certainty: Certainty = Certainty.NOT_DISPATCHED
) -> EnvironmentProviderError:
    """Native transports answer by dispatch certainty, not by the failing category."""
    return provider_error(
        key,
        code,
        category,
        certainty=certainty,
        recovery_hint=(
            Recovery.RECONCILE
            if certainty == Certainty.UNKNOWN
            else Recovery.FIX_INPUT
            if certainty == Certainty.NOT_DISPATCHED
            else Recovery.NONE
        ),
        description="Native environment operation failed.",
    )
