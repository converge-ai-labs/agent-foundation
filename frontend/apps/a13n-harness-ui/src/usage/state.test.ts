import { expect, it } from "vitest";
import {
  compactNumber,
  estimatedCost,
  presetRange,
  rangeError,
  shiftDay,
  usageFilters,
  usageLink,
} from "./state";

it.each([
  [0, "0"],
  [999, "999"],
  [1000, "1K"],
  [123357, "123.36K"],
  [999999, "1M"],
  [37385116, "37.39M"],
  [11931980116, "11.93B"],
])("formats %s as %s", (value, expected) => {
  expect(compactNumber(value as number)).toBe(expected);
});
it("restores UTC presets and custom scopes from URLs", () => {
  const now = new Date("2026-09-08T00:30:00+09:00");
  expect(presetRange(7, now)).toEqual({
    start: "2026-09-01",
    end: "2026-09-08",
  });
  const filters = usageFilters(
    new URLSearchParams(
      "range=custom&start=2026-09-01&end=2026-09-08&projectless=true&thread_id=memory&model=historical&group_by=model&order_by=cost&offset=25",
    ),
    now,
  );
  expect(filters).toMatchObject({
    start: "2026-09-01",
    end: "2026-09-08",
    projectless: true,
    thread_id: "memory",
    model: "historical",
    group_by: "model",
    order_by: "cost",
    offset: 25,
  });
  expect(rangeError(filters)).toBeUndefined();
  expect(shiftDay("2024-02-28", 1)).toBe("2024-02-29");
  expect(usageLink({ projectId: null })).toBe("/usage?projectless=true");
  expect(usageLink({ threadId: "thread/a" })).toBe(
    "/usage?thread_id=thread%2Fa",
  );
});
it("rejects malformed and oversized ranges and conflicting scopes", () => {
  for (const search of [
    "start=2026-02-30&end=2026-03-01",
    "start=2025-01-01&end=2026-01-03",
    "start=&end=",
    "start=2026-09-08&end=2026-09-08",
    "start=2026-09-01&end=2026-09-08&project_id=p&projectless=true",
  ])
    expect(
      rangeError(usageFilters(new URLSearchParams(`range=custom&${search}`))),
    ).toBeTruthy();
});
it("keeps missing, partial, tiny and recorded zero costs distinct", () => {
  const totals = {
    model_requests: 1,
    input_tokens: 0,
    output_tokens: 0,
    total_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    unknown_model_costs: 0,
    model_cost_usd: "0",
  };
  expect(estimatedCost(totals)).toBe("$0.00");
  const large = {
    ...totals,
    model_cost_usd: "17047.94",
    unknown_model_costs: 1,
  };
  expect(estimatedCost(large)).toBe("$17.05K+");
  expect(estimatedCost(large, false)).toBe("$17,047.94+");
  expect(
    estimatedCost({ ...totals, model_cost_usd: null, unknown_model_costs: 1 }),
  ).toBe("—");
  expect(estimatedCost({ ...totals, model_cost_usd: "0.00000001" })).toBe(
    "<$0.0001",
  );
  expect(
    estimatedCost({
      ...totals,
      model_requests: 2,
      model_cost_usd: "1.25",
      unknown_model_costs: 1,
    }),
  ).toBe("$1.25+");
});
