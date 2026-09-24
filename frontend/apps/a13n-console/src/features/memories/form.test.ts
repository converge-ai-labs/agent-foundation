import { expect, it } from "vitest";
import type { Schema } from "../../shared/api";
import {
  guideDraft,
  guideValue,
  labelsError,
  memoryCreate,
  memoryDraft,
  memoryUpdate,
  parseLabels,
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

it("reads labels as key:value pairs", () => {
  expect(parseLabels("team:docs, tier: gold\nurl:a:b")).toEqual({
    team: "docs",
    tier: "gold",
    url: "a:b",
  });
  expect(parseLabels(" ")).toEqual({});
  expect(labelsError("team")).toBe("Write each label as key:value.");
  expect(labelsError(":docs")).toBe("Write each label as key:value.");
  expect(labelsError("team:docs")).toBeUndefined();
});

it("creates a PostgreSQL file memory from the draft", () => {
  expect(
    memoryCreate("handbook", {
      ...memoryDraft(),
      name: " Handbook ",
      labels: "team:docs",
      guide: { mode: "none", text: "" },
      alwaysLoad: ["README.md"],
    }),
  ).toEqual({
    key: "handbook",
    type: "postgres",
    name: "Handbook",
    description: null,
    labels: { team: "docs" },
    guide: "",
    always_load: ["README.md"],
  });
});

it("updates only the fields the draft changed", () => {
  const draft = memoryDraft(saved);
  expect(memoryUpdate(saved, draft)).toEqual({});
  expect(
    memoryUpdate(saved, { ...draft, labels: "tier:gold, team:docs" }),
  ).toEqual({});
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
