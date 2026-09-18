import { expect, it } from "vitest";
import type { Schema } from "../transport/client";
import { elapsedTime, usageSummary } from "./usage";

const totals: Schema<"UsageTotals"> = {
  model_requests: 2,
  provider_receipts: 0,
  tokens: [
    ["input_tokens", 800],
    ["output_tokens", 200],
    ["cache_read_tokens", 600],
    ["cache_write_tokens", 100],
  ],
  model_cost_usd: "0.025",
  unknown_model_costs: 0,
  provider_costs: [],
  unknown_provider_costs: 0,
  omitted_currency_receipts: 0,
};
const usage: Schema<"ThreadUsageView"> = {
  thread_id: "thread-one",
  first_observed_at: "2026-01-01T00:00:00Z",
  observed_through: "2026-01-01T00:01:00Z",
  root: totals,
  descendants: {
    ...totals,
    tokens: [["input_tokens", 9000]],
    model_cost_usd: "100",
  },
  combined: {
    ...totals,
    tokens: [["input_tokens", 10000]],
    model_cost_usd: "100.025",
  },
  models: [],
  other_models: totals,
  recent_runs: [],
  other_runs: totals,
};
it("matches root CLI accounting rather than descendant totals or cache/input alone", () => {
  expect(
    usageSummary(usage, {
      thread_id: "thread-one",
      latest_request_tokens: 25000,
      context_window: 100000,
    }),
  ).toEqual({
    totalTokens: 1000,
    tokens: "1.0K",
    context: "25%",
    cost: "$0.0250",
    cache: "60.0%",
  });
});
it("distinguishes missing usage from zero, partial costs and tiny nonzero costs", () => {
  expect(usageSummary()).toEqual({
    totalTokens: undefined,
    tokens: "—",
    context: "—",
    cost: "—",
    cache: "—",
  });
  expect(usageSummary({ ...usage, first_observed_at: null }).cost).toBe("—");
  expect(
    usageSummary({ ...usage, root: { ...totals, unknown_model_costs: 2 } })
      .cost,
  ).toBe("—");
  expect(
    usageSummary({ ...usage, root: { ...totals, unknown_model_costs: 1 } })
      .cost,
  ).toBe("$0.0250+");
  expect(
    usageSummary({ ...usage, root: { ...totals, model_cost_usd: "0" } }).cost,
  ).toBe("$0.0000");
  expect(
    usageSummary({ ...usage, root: { ...totals, model_cost_usd: "0.000001" } })
      .cost,
  ).toBe("<$0.0001");
  expect(
    usageSummary({ ...usage, root: { ...totals, tokens: [] } }).cache,
  ).toBe("—");
  expect(
    usageSummary(usage, {
      thread_id: "thread-one",
      context_window: 0,
      latest_request_tokens: 1,
    }).context,
  ).toBe("—");
});
it.each([
  [0, "0"],
  [999, "999"],
  [1000, "1.0K"],
  [12345, "12.3K"],
  [1000000, "1.0M"],
  [1250000, "1.3M"],
])("formats %i total tokens like the CLI as %s", (total, text) => {
  const summary = usageSummary({
    ...usage,
    root: { ...totals, tokens: [["input_tokens", total]] },
  });
  expect(summary.totalTokens).toBe(total);
  expect(summary.tokens).toBe(text);
});
it("keeps unobserved model usage unavailable without losing observed zero", () => {
  expect(usageSummary({ ...usage, first_observed_at: null }).tokens).toBe("—");
  expect(
    usageSummary({ ...usage, root: { ...totals, model_requests: 0 } }).tokens,
  ).toBe("—");
  expect(
    usageSummary({ ...usage, root: { ...totals, tokens: [] } }).tokens,
  ).toBe("0");
});
const operation: Schema<"RootOperationView"> = {
  receipt: {
    receipt_id: "receipt-one",
    thread_id: "thread-one",
    submitted_at: "2026-01-01T00:00:00Z",
  },
  status: "running",
  started_at: "2026-01-01T00:00:00Z",
};
it("uses operation timestamps and stops at completion without inventing missing timings", () => {
  const now = Date.parse("2026-01-01T01:02:03Z");
  expect(elapsedTime(operation, now)).toBe("1h 2m");
  expect(
    elapsedTime(
      {
        ...operation,
        status: "completed",
        completed_at: "2026-01-01T00:01:05Z",
      },
      now,
    ),
  ).toBe("1m 5s");
  expect(elapsedTime({ ...operation, status: "completed" }, now)).toBe("—");
  expect(
    elapsedTime({ ...operation, started_at: null, status: "preparing" }, now),
  ).toBe("1h 2m");
  expect(elapsedTime({ ...operation, started_at: "bad" }, now)).toBe("—");
  expect(elapsedTime(undefined, now)).toBe("—");
});
