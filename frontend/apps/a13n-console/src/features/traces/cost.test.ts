import { expect, it } from "vitest";
import type { Schema } from "../../shared/api";
import { costColumnFormat, formatCost } from "../../shared/cost";
import { observationCost } from "./cost";
import { UNKNOWN } from "../../shared/unknown";

function cost(id: string, value: string | null) {
  return { id, cost_usd: value } as Schema["Span"];
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
  expect(formatCost("3e-12")).toBe("$0.000000000003");
  expect(formatCost("0")).toBe("$0.00");
  expect(formatCost(null)).toBe(UNKNOWN);
  expect(formatCost("0.30000000000000000001")).toBe("$0.30");
  for (const value of ["NaN", "Infinity", "", "unavailable"]) {
    expect(observationCost([cost("a", value)])).toEqual({
      total: null,
      reported: 0,
    });
    expect(formatCost(value)).toBe(UNKNOWN);
  }
});

it("gives a column of costs one decimal count so the digits align", () => {
  const subCent = costColumnFormat(["0.0035", "0.03", "0", null, "0.00001"]);
  expect(["0.0035", "0.03", "0", "0.00001"].map(subCent)).toEqual([
    "$0.0035",
    "$0.0300",
    "$0.0000",
    "<$0.0001",
  ]);
  expect(subCent(null)).toBe(UNKNOWN);
  const dollars = costColumnFormat(["1.5", "0.0035"]);
  expect(["1.5", "0.0035"].map(dollars)).toEqual(["$1.50", "<$0.01"]);
  expect(costColumnFormat(["0.03", "0.25"])("0.03")).toBe("$0.03");
});
