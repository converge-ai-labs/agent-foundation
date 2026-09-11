import Decimal from "decimal.js-light";
import type { Schema } from "../../shared/api";
import { durationMs } from "./values";

export type ObservationSort = {
  field: "started" | "duration" | "cost";
  direction: "asc" | "desc";
};

/** Current-page/loaded values only. Unknowns stay last and ties retain input order. */
export function compareValues(
  left: number | string | null,
  right: number | string | null,
  direction: ObservationSort["direction"],
): number {
  if (left === null) return right === null ? 0 : 1;
  if (right === null) return -1;
  const result =
    typeof left === "string" && typeof right === "string"
      ? new Decimal(left).comparedTo(new Decimal(right))
      : Number(left) - Number(right);
  return direction === "asc" ? result : -result;
}

export function compareObservations(
  left: Schema["Observation"],
  right: Schema["Observation"],
  sort: ObservationSort,
): number {
  const value = (observation: Schema["Observation"]) =>
    sort.field === "cost"
      ? observation.cost_usd
      : sort.field === "duration"
        ? durationMs(observation)
        : Date.parse(observation.started_at);
  return compareValues(value(left), value(right), sort.direction);
}
