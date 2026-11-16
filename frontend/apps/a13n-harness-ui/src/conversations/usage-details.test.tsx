// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DebugProvider } from "../shell/debug";
import type { Schema } from "../transport/client";
import { ContextDetails, CostDetails, TokenDetails } from "./usage-details";

afterEach(() => {
  cleanup();
  localStorage.clear();
  delete document.documentElement.dataset.debug;
});

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
  expect(screen.getByRole("meter").getAttribute("aria-valuetext")).toBe(
    "25,000 of 100,000 tokens",
  );
  view.rerender(<ContextDetails used={25000} />);
  expect(number("Used tokens")).toBe("25,000");
  expect(screen.queryByText("Context window")).toBeNull();
  expect(screen.queryByRole("meter")).toBeNull();
  expect(screen.getByText("Context limit unavailable.")).toBeTruthy();
  view.rerender(<ContextDetails used={1100} capacity={1000} />);
  expect(number("Remaining tokens")).toBe("0");
  expect(number("Over capacity")).toBe("100");
  expect(screen.getByRole("meter").getAttribute("aria-valuenow")).toBe("1000");
  expect(screen.getByRole("meter").getAttribute("aria-valuetext")).toBe(
    "1,100 of 1,000 tokens",
  );
  view.rerender(<ContextDetails used={0} capacity={1000} />);
  expect(number("Used tokens")).toBe("0");
  expect(number("Remaining tokens")).toBe("1,000");
});

it("switches model scope without treating a subagent media source as extra usage", () => {
  const child = { ...root, model_requests: 1, model_cost_usd: "0.3" };
  render(
    <DebugProvider>
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
              reported_models: ["gateway-media-v2"],
              totals: child,
            },
          ],
        }}
      />
    </DebugProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "This chat" }));
  expect(screen.getByText("provider/root-model")).toBeTruthy();
  expect(screen.queryByText("provider/media-model")).toBeNull();
  expect(number("Model cost - USD")).toBe("$0.1250");
  fireEvent.click(screen.getByRole("button", { name: "Subagents" }));
  expect(screen.queryByText("provider/root-model")).toBeNull();
  expect(screen.getByText("provider/media-model")).toBeTruthy();
  expect(screen.getByText(/Reported models: gateway-media-v2/)).toBeTruthy();
  fireEvent.click(screen.getByText("Agents and sources"));
  expect(
    screen.getByText("Subagent - Media understanding - view"),
  ).toBeTruthy();
  expect(screen.queryByText("agent-child")).toBeNull();
  fireEvent.keyDown(window, {
    key: "d",
    code: "KeyD",
    ctrlKey: true,
    altKey: true,
  });
  expect(screen.getByText("agent-child")).toBeTruthy();
});

it("renders legacy summaries without new attribution fields and does not invent scoped models", () => {
  render(<CostDetails usage={usage} />);
  expect(screen.getByText("provider/root-model")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "This chat" }));
  expect(
    screen.getByText(/Older records don't show which models were used here/),
  ).toBeTruthy();
  expect(number("Model cost - USD")).toBe("$0.1250");
});

it("shows an empty scoped view without unknown-cost placeholders and restores totals on return", () => {
  render(<TokenDetails usage={{ ...usage, descendants: zero }} />);
  fireEvent.click(screen.getByRole("button", { name: "Subagents" }));
  expect(screen.getByText("No usage in this scope yet.")).toBeTruthy();
  expect(screen.queryByText("Total tokens")).toBeNull();
  expect(screen.queryByText("Model cost - USD")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "All" }));
  expect(number("Total tokens")).toBe("15,456");
  expect(screen.queryByText("No usage in this scope yet.")).toBeNull();
});

it("estimates input groups by character weight and measures shares against the full window", () => {
  const request: Schema<"ContextUsageView"> = {
    thread_id: "one",
    input_tokens: 1000,
    output_tokens: 200,
    context_window: 4000,
    context_breakdown: {
      counted_input_characters: 100,
      groups: {
        skills: {
          characters: 25,
          items: {
            sample: { characters: 20 },
          },
        },
        conversation: { characters: 75 },
      },
    },
  };
  const view = render(
    <ContextDetails compact used={1200} capacity={4000} request={request} />,
  );
  expect(screen.getByText("250")).toBeTruthy();
  expect(screen.getByText("6.3%")).toBeTruthy();
  expect(screen.getByRole("meter").getAttribute("aria-valuenow")).toBe("1200");
  expect(screen.queryByRole("button", { name: /Conversation/ })).toBeNull();
  const skills = screen.getByRole("button", { name: /Skills/ });
  expect(skills.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(skills);
  expect(skills.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText("sample")).toBeTruthy();
  view.rerender(
    <ContextDetails
      compact
      used={1200}
      capacity={4000}
      request={{
        ...request,
        context_breakdown: {
          ...request.context_breakdown!,
          unmeasured_content: ["other"],
        },
      }}
    />,
  );
  expect(screen.getByText("250")).toBeTruthy();
  expect(screen.getByText("Of window")).toBeTruthy();
  const segments = screen.getByRole("meter").children;
  expect(segments).toHaveLength(3);
  expect((segments[0] as HTMLElement).style.width).toBe("6.25%");
  expect((segments[1] as HTMLElement).style.width).toBe("18.75%");
  view.rerender(
    <ContextDetails
      compact
      used={1200}
      capacity={4000}
      request={{
        ...request,
        input_audio_tokens: 100,
        context_breakdown: {
          ...request.context_breakdown!,
          unmeasured_content: ["audio", "other"],
        },
      }}
    />,
  );
  expect(screen.getByText("225")).toBeTruthy();
  expect(screen.getByText("5.6%")).toBeTruthy();
});

it("expands each measured source with request-local estimates and mount repetition counts", () => {
  const request: Schema<"ContextUsageView"> = {
    thread_id: "one",
    input_tokens: 1000,
    context_window: 4000,
    context_breakdown: {
      counted_input_characters: 100,
      groups: {
        environment_context: {
          characters: 30,
          items: {
            "Environment mounts": {
              characters: 30,
              occurrences: 3,
              distinct_versions: 2,
            },
          },
        },
        runtime_state: { characters: 40, items: { Notes: { characters: 40 } } },
        conversation: {
          characters: 30,
          items: { User: { characters: 5 }, Agent: { characters: 25 } },
        },
      },
    },
  };
  render(
    <ContextDetails compact used={1000} capacity={4000} request={request} />,
  );
  for (const name of [/Environment context/, /Working state/, /Conversation/]) {
    fireEvent.click(screen.getByRole("button", { name }));
  }
  expect(
    screen.getByText("Occurrences: 3 · Distinct versions: 2"),
  ).toBeTruthy();
  expect(screen.getByText("Notes")).toBeTruthy();
  expect(screen.getByText("User").parentElement?.textContent).toContain("50");
  expect(screen.getByText("Agent").parentElement?.textContent).toContain("250");
});
