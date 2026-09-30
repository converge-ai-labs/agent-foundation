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

const cents = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/**
 * Readable USD: cents from one cent up, two significant digits below it, so
 * small per-run costs stay comparable without a column of eight-digit tails.
 * `exactCost` keeps the full reported value for titles and copies.
 */
export function formatCost(value: string | null): string {
  if (value === null) return UNKNOWN;
  try {
    const cost = new Cost(value);
    if (cost.isZero() || cost.abs().gte("0.01"))
      return `$${cents.format(Number(cost.toFixed(2)))}`;
    return `$${cost.toSignificantDigits(2).toFixed()}`;
  } catch {
    return UNKNOWN;
  }
}

export function exactCost(value: string | null): string | undefined {
  if (value === null) return undefined;
  try {
    return `$${new Cost(value).toFixed()}`;
  } catch {
    return undefined;
  }
}
