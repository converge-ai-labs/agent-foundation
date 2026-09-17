import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { ModelPricing } from "./model-pricing";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values: Record<string, unknown> = {}) =>
      key.replace(/\{\{(\w+)\}\}/g, (_, name: string) =>
        String(values[name] ?? name),
      ),
  }),
}));
afterEach(cleanup);

function Editor() {
  const [pricing, setPricing] = useState<Schema["TokenPricing-Input"] | null>(
    null,
  );
  return (
    <>
      <ModelPricing value={pricing} onChange={setPricing} />
      <output data-testid="value">{JSON.stringify(pricing)}</output>
    </>
  );
}

it("edits decimal rates, retains zero, and clears unknown prices", async () => {
  render(<Editor />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Pricing/ }));
  await user.type(screen.getByLabelText("Tier 1: Input"), "0");
  expect(JSON.parse(screen.getByTestId("value").textContent!)).toMatchObject({
    tiers: [{ rates: { input: "0" } }],
  });
  await user.clear(screen.getByLabelText("Tier 1: Input"));
  expect(JSON.parse(screen.getByTestId("value").textContent!)).toMatchObject({
    tiers: [{ rates: { input: null } }],
  });
  await user.click(screen.getByRole("button", { name: "Clear prices" }));
  expect(screen.getByTestId("value").textContent).toBe("null");
});

it("adds and removes explicit threshold rows without inheriting prices", async () => {
  render(<Editor />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Pricing/ }));
  await user.type(screen.getByLabelText("Tier 1: Input"), "5");
  await user.click(screen.getByRole("button", { name: "Add price tier" }));
  await user.clear(screen.getByLabelText("Tier 2: above input tokens"));
  await user.type(
    screen.getByLabelText("Tier 2: above input tokens"),
    "200000",
  );
  await user.type(screen.getByLabelText("Tier 2: Output"), "45");
  expect(JSON.parse(screen.getByTestId("value").textContent!)).toMatchObject({
    tiers: [
      { above: null, rates: { input: "5" } },
      { above: 200000, rates: { output: "45" } },
    ],
  });
  await user.click(screen.getByRole("button", { name: "Remove tier 2" }));
  expect(screen.queryByLabelText("Tier 2: above input tokens")).toBeNull();
});
