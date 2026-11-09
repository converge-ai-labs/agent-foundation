import { expect, it } from "vitest";
import type { Schema } from "../../shared/api";
import {
  defaultGuidance,
  guidanceModules,
  guidancePreview,
  selectSystemPreset,
} from "./guidance";
import {
  policyDraft,
  policyValue,
  validHistory,
  validPolicy,
} from "./organization-form";

const history: Schema["OrganizationHistory"] = {
  session_ids: ["sess_source"],
  after: "2026-11-01T00:00:00Z",
  before: null,
  max_runs: 100,
};
it("preserves explicit instructions and history across policy edits", () => {
  const policy: Schema["OrganizationPolicy"] = {
    enabled: true,
    model: "local",
    instructions: "Keep decisions.",
    history,
    trigger: { type: "change_count", count: 12 },
    check_interval_seconds: 30,
    cooldown_seconds: 0,
  };
  expect(policyValue(policyDraft(policy))).toEqual(policy);
  const draft = { ...policyDraft(policy), cooldown: "300" };
  expect(policyValue(draft)).toEqual({ ...policy, cooldown_seconds: 300 });
  expect(validPolicy(draft)).toBe(true);
  expect(validPolicy({ ...draft, instructions: "a".repeat(16385) })).toBe(
    false,
  );
  expect(policyValue({ ...draft, instructions: "  " })).toMatchObject({
    instructions: null,
  });
});
it("validates bounded explicit history and an intentional automatic start", () => {
  expect(validHistory(null, true)).toBe(true);
  expect(validHistory({ ...history, after: null })).toBe(true);
  expect(validHistory({ ...history, after: null }, true)).toBe(false);
  for (const invalid of [
    { session_ids: [] },
    { session_ids: ["s", "s"] },
    { session_ids: Array.from({ length: 101 }, (_, i) => `s${i}`) },
    { after: "invalid" },
    { before: "2026-10-01T00:00:00Z" },
    { max_runs: 0 },
    { max_runs: 1001 },
    { max_runs: 1.5 },
  ])
    expect(validHistory({ ...history, ...invalid })).toBe(false);
  expect(
    validHistory({ ...history, before: history.after, max_runs: 1000 }, true),
  ).toBe(true);
});
it("previews inherited, explicit-empty and overridden modules using catalog order", () => {
  const catalog: Schema["MemoryGuidancePresets"] = {
    items: [
      {
        id: "general",
        name: "General",
        description: "",
        system_guide: "common;topics;",
      },
      {
        id: "journal",
        name: "Journal",
        description: "",
        system_guide: "common;topics;journal;",
      },
    ],
    modules: [
      { id: "topics", name: "Topics", description: "", guide: "topics;" },
      { id: "journal", name: "Journal", description: "", guide: "journal;" },
      { id: "session_notes", name: "Notes", description: "", guide: "notes;" },
    ],
  };
  const guidance = {
    ...defaultGuidance(),
    system_preset: "journal" as const,
    custom: "Keep facts.",
  };
  expect(guidanceModules(guidance)).toEqual(["topics", "journal"]);
  expect(guidancePreview(guidance, catalog)).toBe("common;topics;journal;");
  expect(guidancePreview({ ...guidance, modules: [] }, catalog)).toBe(
    "common;",
  );
  expect(
    guidancePreview(
      { ...guidance, modules: ["session_notes", "topics"] },
      catalog,
    ),
  ).toBe("common;topics;notes;");
  expect(
    guidancePreview({ ...guidance, modules: [], system_override: "" }, catalog),
  ).toBe("");
  expect(
    selectSystemPreset(
      { ...guidance, modules: [], system_override: "Custom" },
      "general",
    ),
  ).toEqual({
    ...guidance,
    system_preset: "general",
    modules: null,
    system_override: null,
  });
});
