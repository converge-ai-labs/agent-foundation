import type { Observation } from "./overview";
import { sumCosts } from "../../shared/cost";

/** Sum each observation's own reported cost once, never token-derived estimates. */
export function observationCost(observations: readonly Observation[]) {
  const unique = new Map(observations.map((item) => [item.id, item]));
  return sumCosts([...unique.values()].map((item) => item.cost_usd));
}
