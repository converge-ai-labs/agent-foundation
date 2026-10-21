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

/**
 * One decimal count for a column, so the digits align: four when a reported
 * value is below a cent and none reaches $1, otherwise cents. A value too
 * small for the last digit reads as below it, never as zero.
 */
export function costColumnFormat(values: Iterable<string | null>) {
  const costs = [...values].flatMap((value) => {
    try {
      return value === null ? [] : [new Cost(value).abs()];
    } catch {
      return [];
    }
  });
  const digits =
    costs.some((cost) => !cost.isZero() && cost.lt("0.01")) &&
    !costs.some((cost) => cost.gte(1))
      ? 4
      : 2;
  const format = new Intl.NumberFormat("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return (value: string | null) => {
    if (value === null) return UNKNOWN;
    try {
      const cost = new Cost(value);
      const fixed = cost.toFixed(digits);
      return !cost.isZero() && new Cost(fixed).isZero()
        ? `<$${(10 ** -digits).toFixed(digits)}`
        : `$${format.format(Number(fixed))}`;
    } catch {
      return UNKNOWN;
    }
  };
}

/**
 * Axis ticks share one precision: cents, or the decimals the finest tick
 * needs, so a sub-cent scale reads $0.015, $0.010, $0.005, $0.000.
 */
export function formatCostTicks(ticks: readonly number[]): string[] {
  const digits = Math.max(
    2,
    ...ticks.map(
      (tick) => tick.toFixed(12).replace(/0+$/, "").split(".")[1].length,
    ),
  );
  const format = new Intl.NumberFormat("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return ticks.map((tick) => `$${format.format(tick)}`);
}

export function exactCost(value: string | null): string | undefined {
  if (value === null) return undefined;
  try {
    return `$${new Cost(value).toFixed()}`;
  } catch {
    return undefined;
  }
}
