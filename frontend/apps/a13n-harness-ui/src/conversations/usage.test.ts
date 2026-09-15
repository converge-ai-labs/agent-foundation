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
  descendants: { ...totals, model_cost_usd: "100" },
  combined: { ...totals, model_cost_usd: "100.025" },
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
  ).toEqual({ context: "25%", cost: "$0.0250", cache: "60.0%" });
});
it("distinguishes missing usage from zero, partial costs and tiny nonzero costs", () => {
  expect(usageSummary()).toEqual({ context: "—", cost: "—", cache: "—" });
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
  expect(elapsedTime({ ...operation, started_at: null }, now)).toBe("—");
  expect(elapsedTime({ ...operation, started_at: "bad" }, now)).toBe("—");
  expect(elapsedTime(undefined, now)).toBe("—");
});
