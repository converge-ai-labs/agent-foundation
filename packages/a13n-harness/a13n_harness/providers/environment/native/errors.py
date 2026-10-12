"""Safe native transport errors with explicit uncertain-mutation outcomes."""

from a13n_harness.environment.errors import EnvironmentProviderError, provider_error
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.environment.errors import EnvironmentProviderRecoveryHint as Recovery


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
