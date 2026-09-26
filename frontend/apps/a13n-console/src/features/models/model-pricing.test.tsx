import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import {
  ModelPricing,
  priceEntry,
  priceTable,
  type PriceTable,
} from "./model-pricing";

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
  const [pricing, setPricing] = useState<PriceTable | null>(null);
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
    tiers: [{ rates: { input_mtok: "0" } }],
  });
  await user.clear(screen.getByLabelText("Tier 1: Input"));
  expect(JSON.parse(screen.getByTestId("value").textContent!)).toMatchObject({
    tiers: [{ rates: { input_mtok: null } }],
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
      { above: null, rates: { input_mtok: "5" } },
      { above: 200000, rates: { output_mtok: "45" } },
    ],
  });
  await user.click(screen.getByRole("button", { name: "Remove tier 2" }));
  expect(screen.queryByLabelText("Tier 2: above input tokens")).toBeNull();
});

const catalogPrice: Schema["ModelPricingEntry-Output"] = {
  provider: "anthropic",
  model: "claude-opus-5",
  context_window: 1000000,
  source: "genai_prices",
  source_revision: "2026-09-01",
  source_url: "https://example.com/pricing",
  rules: [
    {
      rule_id: "standard",
      constraint: { kind: "always" },
      prices: [
        {
          price_key: "input_mtok",
          price: "5",
          tiers: [{ start: 200000, price: "10" }],
        },
        { price_key: "output_mtok", price: "25" },
        { price_key: "cache_write_1h_mtok", price: "10" },
      ],
    },
  ],
};

it("reads the standard rule's token prices into threshold rows", () => {
  expect(priceTable(catalogPrice)).toEqual({
    tiers: [
      { above: null, rates: { input_mtok: "5", output_mtok: "25" } },
      { above: 200000, rates: { input_mtok: "10" } },
    ],
  });
  expect(priceTable(null)).toBeNull();
});

it("saves an edited table under the entry it was read from without dropping prices the editor does not show", () => {
  const table = priceTable(catalogPrice)!;
  table.tiers[0].rates.output_mtok = "30";
  expect(
    priceEntry(table, catalogPrice, {
      provider: "openai",
      model: "gateway-opus",
    }),
  ).toEqual({
    provider: "anthropic",
    model: "claude-opus-5",
    context_window: 1000000,
    source: "console",
    source_revision: "manual",
    rules: [
      {
        rule_id: "standard",
        constraint: { kind: "always" },
        prices: [
          { price_key: "cache_write_1h_mtok", price: "10" },
          {
            price_key: "input_mtok",
            price: "5",
            tiers: [{ start: 200000, price: "10" }],
          },
          { price_key: "output_mtok", price: "30", tiers: [] },
        ],
      },
    ],
  });
});

it("edits only base prices and preserves service-tier rules and their limits", () => {
  const priority = {
    rule_id: "priority",
    service_tier: "priority",
    max_input_tokens: 272000,
    prices: [{ price_key: "input_mtok", price: "20" }],
  };
  const entry = { ...catalogPrice, rules: [...catalogPrice.rules, priority] };
  const table = priceTable(entry)!;
  expect(table).toEqual(priceTable(catalogPrice));
  table.tiers[0].rates.input_mtok = "6";
  const saved = priceEntry(table, entry, { provider: "openai", model: "test" });
  expect(saved?.rules[1]).toEqual(priority);
  expect(saved?.rules[0].prices).toContainEqual({
    price_key: "input_mtok",
    price: "6",
    tiers: [{ start: 200000, price: "10" }],
  });
});

it("names hand-entered prices after the model and clears an empty table", () => {
  const table: PriceTable = {
    tiers: [{ above: null, rates: { input_mtok: "1", output_mtok: null } }],
  };
  expect(
    priceEntry(table, null, { provider: "openai", model: "company-smart" }),
  ).toMatchObject({
    provider: "openai",
    model: "company-smart",
    rules: [
      {
        rule_id: "standard",
        prices: [{ price_key: "input_mtok", price: "1", tiers: [] }],
      },
    ],
  });
  expect(
    priceEntry({ tiers: [{ above: null, rates: {} }] }, null, {
      provider: "openai",
      model: "company-smart",
    }),
  ).toBeNull();
});
