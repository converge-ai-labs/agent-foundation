import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { UsageChart } from "./chart";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(cleanup);
const usage = (
  cost: string | null,
  input_tokens = 100,
): Schema["ModelMetrics"] => ({
  requests: 1,
  input_tokens,
  output_tokens: 0,
  cache_read_tokens: 0,
  cache_hit_rate: 0,
  cost,
  unpriced_requests: cost === null ? 1 : 0,
});
const day = (cost: string | null, input?: number) => ({
  date: "2026-09-28",
  usage: usage(cost, input),
});
function mount(metric: "cost" | "tokens" = "cost") {
  render(
    <UsageChart
      days={[
        {
          ...day("10", 1000),
          usage: { ...usage("10", 1000), unpriced_requests: 1 },
        },
      ]}
      series={[
        { key: "others", name: "Model A", daily: [day("6", 600)] },
        { key: "model-b", name: "Model B", daily: [day(null, 100)] },
        { key: null, name: null, daily: [day("4", 300)] },
      ]}
      metric={metric}
      onMetricChange={vi.fn()}
      group="models"
      onGroupChange={vi.fn()}
    />,
  );
}
it("opens daily shares on hover, dismisses with Escape and preserves unknown cost", async () => {
  const user = userEvent.setup();
  mount();
  const bar = screen.getByRole("button", { name: /Sep 28: \$10/ });
  await user.hover(bar);
  const popup = await screen.findByRole("dialog", { name: "Sep 28" });
  expect(within(popup).getByText("60%")).toBeTruthy();
  expect(within(popup).getByText("40%")).toBeTruthy();
  expect(within(popup).getByText("Others")).toBeTruthy();
  expect(within(popup).getByText("Some requests are unpriced")).toBeTruthy();
  const unknown = within(popup).getByText("Model B").closest("li")!;
  expect(within(unknown).getAllByText("—")).toHaveLength(2);
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});
it("supports click and keyboard inspection with token shares", async () => {
  const user = userEvent.setup();
  mount("tokens");
  const bar = screen.getByRole("button", { name: /Sep 28: 1,000/ });
  await user.click(bar);
  // Click remains available on devices without hover.
  const popup = await screen.findByRole("dialog", { name: "Sep 28" });
  expect(within(popup).getByText("10%")).toBeTruthy();
  expect(within(popup).getByText("30%")).toBeTruthy();
  expect(within(popup).queryByText("Some requests are unpriced")).toBeNull();
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await user.keyboard("{Enter}");
  expect(await screen.findByRole("dialog", { name: "Sep 28" })).toBeTruthy();
});
