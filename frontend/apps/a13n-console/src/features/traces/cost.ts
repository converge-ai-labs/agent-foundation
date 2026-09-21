import type { Schema } from "../../shared/api";
import { sumCosts } from "../../shared/cost";

/** Sum each observation's own reported cost once, never token-derived estimates. */
export function observationCost(
  observations: readonly Schema["Observation"][],
) {
  const unique = new Map(observations.map((item) => [item.id, item]));
  return sumCosts([...unique.values()].map((item) => item.cost_usd));
}
