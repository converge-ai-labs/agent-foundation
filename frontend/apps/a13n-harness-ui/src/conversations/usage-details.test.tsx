// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import type { Schema } from "../transport/client";
import { ContextDetails, CostDetails, TokenDetails } from "./usage-details";

afterEach(cleanup);

const root: Schema<"UsageTotals"> = {
  model_requests: 2,
  provider_receipts: 0,
  tokens: [
    ["input_tokens", 12000],
    ["output_tokens", 3456],
    ["cache_read_tokens", 8000],
    ["cache_write_tokens", 1000],
  ],
  model_cost_usd: "0.125",
  unknown_model_costs: 0,
  provider_costs: [],
  unknown_provider_costs: 0,
  omitted_currency_receipts: 0,
};
const zero = { ...root, model_requests: 0, tokens: [], model_cost_usd: "0" };
const usage: Schema<"ThreadUsageView"> = {
  thread_id: "one",
  first_observed_at: "2026-01-01T00:00:00Z",
  observed_through: "2026-01-01T00:01:00Z",
  root,
  descendants: { ...root, model_cost_usd: "0.3", unknown_model_costs: 1 },
  combined: {
    ...root,
    model_requests: 4,
    model_cost_usd: "0.425",
    unknown_model_costs: 1,
  },
  models: [
    ["provider/root-model", root],
    [
      "provider/child-model",
      { ...root, model_requests: 1, model_cost_usd: "0.3" },
    ],
  ],
  other_models: { ...zero, model_requests: 1, unknown_model_costs: 1 },
  recent_runs: [],
  other_runs: zero,
};

function number(label: string) {
  return screen.getByText(label, { selector: "dt" }).nextElementSibling!
    .textContent;
}

it("shows actual context counts, unknown capacity and over-capacity observations", () => {
  const view = render(<ContextDetails used={25000} capacity={100000} />);
  expect(number("Used tokens")).toBe("25,000");
  expect(number("Context window")).toBe("100,000");
  expect(number("Remaining tokens")).toBe("75,000");
  view.rerender(<ContextDetails used={25000} />);
  expect(number("Context window")).toBe("Unknown");
  expect(number("Remaining tokens")).toBe("Unknown");
  view.rerender(<ContextDetails used={1100} capacity={1000} />);
  expect(number("Remaining tokens")).toBe("0");
  expect(number("Over capacity")).toBe("100");
  view.rerender(<ContextDetails used={0} capacity={1000} />);
  expect(number("Used tokens")).toBe("0");
  expect(number("Remaining tokens")).toBe("1,000");
});

it("shows root token and cache counters without double counting or descendant totals", () => {
  render(<TokenDetails usage={usage} />);
  expect(number("Total tokens")).toBe("15,456");
  expect(number("Input tokens")).toBe("12,000");
  expect(number("Output tokens")).toBe("3,456");
  expect(number("Cache read")).toBe("8,000");
  expect(number("Cache write")).toBe("1,000");
  expect(number("Model requests")).toBe("2");
});

it("distinguishes missing usage from recorded zero counters", () => {
  const view = render(<TokenDetails />);
  expect(
    screen.getByText("No recorded root-agent token usage yet."),
  ).toBeTruthy();
  view.rerender(
    <TokenDetails usage={{ ...usage, root: { ...root, tokens: [] } }} />,
  );
  expect(number("Total tokens")).toBe("0");
  view.rerender(<CostDetails />);
  expect(screen.getByText("No recorded model costs yet.")).toBeTruthy();
});

it("breaks model costs down with explicit combined scope and partial/unknown amounts", () => {
  render(<CostDetails usage={usage} />);
  expect(number("Root agent")).toBe("$0.1250");
  expect(number("Subagents")).toContain("$0.3000+");
  expect(number("Combined")).toContain("$0.4250+");
  expect(screen.getByText("Root + subagents · USD")).toBeTruthy();
  const models = screen.getAllByRole("listitem");
  expect(within(models[0]).getByText("provider/root-model")).toBeTruthy();
  expect(within(models[0]).getByText("$0.1250")).toBeTruthy();
  expect(within(models[1]).getByText("$0.3000")).toBeTruthy();
  expect(within(models[2]).getByText("Other models")).toBeTruthy();
  expect(within(models[2]).getByText("—")).toBeTruthy();
  expect(within(models[2]).getByText("1 unknown-cost response")).toBeTruthy();
});
