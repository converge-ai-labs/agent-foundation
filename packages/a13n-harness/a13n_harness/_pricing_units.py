"""Complete unused pricing intersections for one request without inventing tariffs."""

from decimal import Decimal
from itertools import combinations

from genai_prices.data_units import unit_data
from genai_prices.types import TieredPrices, Usage
from genai_prices.units import UnitRegistry

_UNITS = UnitRegistry(unit_data)


def request_prices(prices: dict[str, Decimal | TieredPrices], usage: Usage) -> dict[str, Decimal | TieredPrices] | None:
    """Keep upstream decomposition valid when an unpriced intersection has exactly zero usage.

    Inclusive buckets such as cached input and audio input overlap. The upstream calculator requires
    every ancestor and intersection price even when that bucket is unused. A temporary zero rate is
    harmless only for a bucket whose usage is known to be zero; positive or ambiguous usage declines.
    """
    prices = prices.copy()
    units = {unit.usage_key: unit for unit in _UNITS.units.values() if unit.price_key in prices}
    while True:
        required = set().union(*(_UNITS.ancestor_usage_keys(key) for key in units))
        for left, right in combinations(units.values(), 2):
            if join := _UNITS.find_join(left, right):
                required.add(join.usage_key)
        missing = required - units.keys()
        if not missing:
            return prices
        for key in sorted(missing):
            try:
                count = getattr(usage, key)
            except ValueError:
                return None
            if count != 0:
                return None
            unit = _UNITS.units[key]
            prices[unit.price_key] = Decimal(0)
            units[key] = unit
