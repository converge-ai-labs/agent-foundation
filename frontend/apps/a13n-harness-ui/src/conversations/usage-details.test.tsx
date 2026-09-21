// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
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
  return screen.getAllByText(label, { selector: "dt" })[0].nextElementSibling!
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

it("shows combined token and cache counters without counting cache twice", () => {
  render(<TokenDetails usage={usage} />);
  expect(number("Total tokens")).toBe("15,456");
  expect(number("Input tokens")).toBe("12,000");
  expect(number("Output tokens")).toBe("3,456");
  expect(number("Cache read")).toBe("8,000");
  expect(number("Cache write")).toBe("1,000");
  expect(number("Model requests")).toBe("4");
});

it("distinguishes missing usage from recorded zero counters", () => {
  const view = render(<TokenDetails />);
  expect(screen.getByText(/No recorded usage yet/)).toBeTruthy();
  view.rerender(
    <TokenDetails usage={{ ...usage, combined: { ...root, tokens: [] } }} />,
  );
  expect(number("Total tokens")).toBe("0");
  view.rerender(<CostDetails />);
  expect(screen.getByText(/No recorded usage yet/)).toBeTruthy();
});

it("breaks model costs down with explicit combined scope and partial/unknown amounts", () => {
  render(<CostDetails usage={usage} />);
  expect(number("Model cost · USD")).toContain("$0.4250+");
  expect(
    screen.getByRole("button", { name: "All" }).getAttribute("aria-pressed"),
  ).toBe("true");
  const models = screen.getAllByRole("listitem");
  expect(within(models[0]).getByText("provider/root-model")).toBeTruthy();
  expect(within(models[0]).getByText("$0.1250")).toBeTruthy();
  expect(within(models[1]).getByText("$0.3000")).toBeTruthy();
  expect(within(models[2]).getByText("Other models")).toBeTruthy();
  expect(within(models[2]).getByText("—")).toBeTruthy();
  expect(within(models[2]).getByText("1 unknown-cost response")).toBeTruthy();
});

it("switches model scope without treating a subagent media source as extra usage", () => {
  const child = { ...root, model_requests: 1, model_cost_usd: "0.3" };
  render(
    <TokenDetails
      usage={{
        ...usage,
        model_scopes: [
          {
            name: "provider/root-model",
            root,
            descendants: zero,
            combined: root,
          },
          {
            name: "provider/media-model",
            root: zero,
            descendants: child,
            combined: child,
          },
        ],
        groups: [
          {
            model: "provider/media-model",
            agent_instance_id: "agent-child",
            descendant: true,
            source: "files.media_understanding",
            totals: child,
          },
        ],
      }}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Root" }));
  expect(screen.getByText("provider/root-model")).toBeTruthy();
  expect(screen.queryByText("provider/media-model")).toBeNull();
  expect(number("Model cost · USD")).toBe("$0.1250");
  fireEvent.click(screen.getByRole("button", { name: "Subagents" }));
  expect(screen.queryByText("provider/root-model")).toBeNull();
  expect(screen.getByText("provider/media-model")).toBeTruthy();
  fireEvent.click(screen.getByText("Agents and sources"));
  expect(
    screen.getByText("Subagent · Media understanding · view"),
  ).toBeTruthy();
  expect(screen.getByText("agent-child")).toBeTruthy();
});

it("renders legacy summaries without new attribution fields and does not invent scoped models", () => {
  render(<CostDetails usage={usage} />);
  expect(screen.getByText("provider/root-model")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Root" }));
  expect(screen.getByText(/Model attribution is unavailable/)).toBeTruthy();
  expect(number("Model cost · USD")).toBe("$0.1250");
});
