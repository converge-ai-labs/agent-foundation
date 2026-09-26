"""Decimal arithmetic shared by usage projections."""

from collections.abc import Iterable
from decimal import Decimal, localcontext


def sum_decimal(values: Iterable[Decimal]) -> Decimal:
    """Sum finite amounts without the ambient Decimal context rounding the total."""
    items = tuple(values)
    if not items:
        return Decimal(0)
    with localcontext() as context:
        places = min(int(value.as_tuple().exponent) for value in items)
        digits = max(value.adjusted() for value in items) - places + len(str(len(items))) + 2
        context.prec = max(28, digits)
        return sum(items, Decimal(0))
