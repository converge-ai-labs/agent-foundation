"""Safe native transport errors with explicit uncertain-mutation outcomes."""

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


def failure(
    key: str, code: str, category: Category, *, certainty: Certainty = Certainty.NOT_DISPATCHED
) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        "Native environment operation failed.",
        code=code,
        category=category,
        certainty=certainty,
        recovery_hint=(
            Recovery.RECONCILE
            if certainty == Certainty.UNKNOWN
            else Recovery.FIX_INPUT
            if certainty == Certainty.NOT_DISPATCHED
            else Recovery.NONE
        ),
        context=EnvironmentProviderErrorContext(provider_key=key),
    )
