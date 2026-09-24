import { expect, it } from "vitest";
import type { Schema } from "../../shared/api";
import {
  guideDraft,
  guideValue,
  memoryCreate,
  memoryDraft,
  memoryUpdate,
  rebaseDraft,
  sameDraft,
} from "./form";

const saved = {
  id: "mem_1",
  name: "Handbook",
  description: "Team rules",
  labels: { team: "docs", tier: "gold" },
  guide: null,
  always_load: ["README.md"],
} as unknown as Schema["Memory"];

it("keeps the three guide states apart", () => {
  expect(guideDraft(null)).toEqual({ mode: "inherit", text: "" });
  expect(guideDraft("")).toEqual({ mode: "none", text: "" });
  expect(guideDraft("Mine")).toEqual({ mode: "custom", text: "Mine" });
  expect(guideValue({ mode: "inherit", text: "hidden" })).toBeNull();
  expect(guideValue({ mode: "none", text: "hidden" })).toBe("");
  expect(guideValue({ mode: "custom", text: "Mine" })).toBe("Mine");
});

it("creates a PostgreSQL file memory from the draft", () => {
  expect(
    memoryCreate("handbook", {
      ...memoryDraft(),
      name: " Handbook ",
      guide: { mode: "none", text: "" },
      alwaysLoad: ["README.md"],
    }),
  ).toEqual({
    key: "handbook",
    type: "postgres",
    name: "Handbook",
    description: null,
    guide: "",
    always_load: ["README.md"],
  });
});

it("creates a record memory on its provider, without always-loaded files", () => {
  const provider = { id: "memprov_1", type: "mem0" } as Schema["Provider"];
  const draft = { ...memoryDraft(), name: "Facts", alwaysLoad: ["README.md"] };
  const record = {
    key: "facts",
    type: "mem0",
    provider_id: "memprov_1",
    name: "Facts",
    description: null,
    guide: null,
  };
  // A blank namespace asks the Service for a new one.
  expect(memoryCreate("facts", draft, { provider, namespace: " " })).toEqual(
    record,
  );
  expect(
    memoryCreate("facts", draft, { provider, namespace: " user-42 " }),
  ).toEqual({ ...record, namespace: "user-42" });
});

it("updates only the fields the draft changed", () => {
  const draft = memoryDraft(saved);
  expect(memoryUpdate(saved, draft)).toEqual({});
  expect(
    memoryUpdate(saved, {
      ...draft,
      description: " ",
      guide: { mode: "custom", text: "Mine" },
      alwaysLoad: [],
    }),
  ).toEqual({ description: null, guide: "Mine", always_load: [] });
});

it("compares drafts by what they would save", () => {
  const draft = memoryDraft(saved);
  expect(
    sameDraft(draft, { ...draft, guide: { mode: "inherit", text: "typed" } }),
  ).toBe(true);
  expect(sameDraft(draft, { ...draft, name: "Other" })).toBe(false);
});

it("carries only the draft's own changes onto a newer memory", () => {
  const previous = memoryDraft(saved);
  const current = memoryDraft({
    ...saved,
    name: "Renamed elsewhere",
    always_load: ["README.md", "rules.md"],
  });
  expect(
    rebaseDraft(previous, { ...previous, description: "Mine" }, current),
  ).toEqual({ ...current, description: "Mine" });
});
