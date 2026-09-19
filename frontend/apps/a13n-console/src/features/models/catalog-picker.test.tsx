import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { CatalogPicker, groupCatalog } from "./catalog-picker";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const entries: Schema["CatalogModel"][] = [
  {
    identity: "openai/gpt-5.5",
    name: "GPT-5.5",
    ref: { provider: "openai", model: "gpt-5.5" },
    provider_name: "OpenAI",
    release_date: "2026-04-23",
    declarations: {},
  },
  ...["au", "us"].map((region) => ({
    identity: "anthropic/claude-opus-5",
    name: "Claude Opus 5",
    ref: {
      provider: "amazon-bedrock",
      model: `${region}.anthropic.claude-opus-5`,
    },
    provider_name: "Bedrock",
    release_date: "2026-05-01",
    declarations: {},
  })),
];
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  HTMLElement.prototype.scrollIntoView = () => {};
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("groups regions without merging different models", () => {
  expect(groupCatalog(entries).map((group) => group.length)).toEqual([2, 1]);
});

it("keeps a large catalog searchable without rendering every model at once", async () => {
  const many = Array.from({ length: 150 }, (_, index) => ({
    ...entries[0],
    identity: `openai/model-${String(index).padStart(3, "0")}`,
    name: `Model ${String(index).padStart(3, "0")}`,
    ref: {
      provider: "openai",
      model: `model-${String(index).padStart(3, "0")}`,
    },
  }));
  const selected = vi.fn();
  render(
    <CatalogPicker
      entries={many}
      channels={["openai"]}
      allowCompatible={false}
      value={null}
      onSelect={selected}
    />,
  );
  const user = userEvent.setup();
  expect(screen.queryByRole("button", { name: /Model 149/ })).toBeNull();
  await user.type(screen.getByRole("searchbox"), "model-149");
  await user.click(await screen.findByRole("button", { name: /Model 149/ }));
  expect(selected).toHaveBeenCalledWith(many[149]);
});

it("shows only provider models until the OpenAI compatible entry is opened", async () => {
  render(
    <CatalogPicker
      entries={entries}
      channels={["openai"]}
      allowCompatible
      value={null}
      onSelect={vi.fn()}
    />,
  );
  const user = userEvent.setup();
  expect(screen.queryByRole("button", { name: /Claude Opus 5/ })).toBeNull();
  await user.click(
    screen.getByRole("button", { name: "Other models (compatible)…" }),
  );
  expect(
    await screen.findByRole("button", { name: /Claude Opus 5/ }),
  ).toBeTruthy();
});

it("requires a concrete regional variant and has no compatible entry for Bedrock", async () => {
  const select = vi.fn();
  render(
    <CatalogPicker
      entries={entries}
      channels={["amazon-bedrock"]}
      allowCompatible={false}
      value={null}
      onSelect={select}
    />,
  );
  const user = userEvent.setup();
  expect(
    screen.queryByRole("button", { name: "Other models (compatible)…" }),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: /Claude Opus 5/ }));
  expect(select).not.toHaveBeenCalled();
  await user.click(
    screen.getByRole("combobox", { name: "Provider model variant" }),
  );
  await user.keyboard("{ArrowDown}");
  await user.click(
    screen.getByRole("option", {
      name: "Bedrock · au.anthropic.claude-opus-5",
    }),
  );
  expect(select).toHaveBeenLastCalledWith(entries[1]);
});

it("offers a custom model when the catalog does not list one", async () => {
  const select = vi.fn();
  render(
    <CatalogPicker
      entries={entries}
      channels={["openai"]}
      allowCompatible={false}
      value={null}
      onSelect={select}
    />,
  );
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: /Custom model/ }));
  expect(select).toHaveBeenCalledWith(null);
});
