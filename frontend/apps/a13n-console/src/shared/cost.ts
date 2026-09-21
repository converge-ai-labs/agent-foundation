import Decimal from "decimal.js-light";
import { UNKNOWN } from "./unknown";

/**
 * Provider and pricing costs are decimal strings. Unreported cost is
 * unavailable, never zero, so sums report how many values were usable.
 */
const Cost = Decimal.clone({ precision: 40 });

export function sumCosts(values: Iterable<string | null | undefined>) {
  let total = new Cost(0);
  let reported = 0;
  for (const value of values) {
    if (value === null || value === undefined) continue;
    try {
      total = total.plus(new Cost(value));
      reported++;
    } catch {
      // Invalid provider values are unavailable, not zero.
    }
  }
  return { total: reported ? total.toString() : null, reported };
}

export function formatCost(value: string | null): string {
  if (value === null) return UNKNOWN;
  try {
    return `$${new Cost(value).toSignificantDigits(8).toString()}`;
  } catch {
    return UNKNOWN;
  }
}
