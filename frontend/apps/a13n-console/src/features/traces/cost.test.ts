import { expect, it } from "vitest";
import type { Schema } from "../../shared/api";
import { formatCost, observationCost } from "./cost";

function cost(id: string, value: string | null) {
  return { id, cost_usd: value } as Schema["Observation"];
}

it("sums reported decimal costs once per observation, including a root's own cost", () => {
  expect(
    observationCost([
      cost("root", "0.1"),
      cost("chat", "0.2"),
      cost("chat", "0.2"),
      cost("tool", null),
    ]),
  ).toEqual({ total: "0.3", reported: 2 });
});

it("distinguishes no reported cost from explicitly reported zero", () => {
  expect(observationCost([cost("root", null)])).toEqual({
    total: null,
    reported: 0,
  });
  expect(observationCost([cost("root", null), cost("chat", "0")])).toEqual({
    total: "0",
    reported: 1,
  });
  expect(observationCost([])).toEqual({ total: null, reported: 0 });
});

it("does not silently round tiny costs to zero or coerce malformed values", () => {
  expect(observationCost([cost("a", "1e-12"), cost("b", "2e-12")]).total).toBe(
    "3e-12",
  );
  expect(formatCost("3e-12")).toBe("$3e-12");
  expect(formatCost("0")).toBe("$0");
  expect(formatCost(null)).toBe("-");
  expect(formatCost("0.30000000000000000001")).toBe("$0.3");
  for (const value of ["NaN", "Infinity", "", "unavailable"]) {
    expect(observationCost([cost("a", value)])).toEqual({
      total: null,
      reported: 0,
    });
    expect(formatCost(value)).toBe("-");
  }
});
