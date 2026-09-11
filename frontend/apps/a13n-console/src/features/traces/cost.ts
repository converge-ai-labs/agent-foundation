import Decimal from "decimal.js-light";
import type { Schema } from "../../shared/api";

const Cost = Decimal.clone({ precision: 40 });

/** Sum each observation's own reported cost once, never token-derived estimates. */
export function observationCost(
  observations: readonly Schema["Observation"][],
) {
  let total = new Cost(0);
  let reported = 0;
  const unique = new Map(observations.map((item) => [item.id, item]));
  for (const observation of unique.values()) {
    if (observation.cost_usd === null) continue;
    try {
      total = total.plus(new Cost(observation.cost_usd));
      reported++;
    } catch {
      // Invalid provider values are unavailable, not zero.
    }
  }
  return { total: reported ? total.toString() : null, reported };
}

export function formatCost(value: string | null): string {
  if (value === null) return "-";
  try {
    return `$${new Cost(value).toSignificantDigits(8).toString()}`;
  } catch {
    return "-";
  }
}
