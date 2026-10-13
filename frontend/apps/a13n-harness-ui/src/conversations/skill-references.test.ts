import { expect, it } from "vitest";
import { CompletionContext } from "@codemirror/autocomplete";
import { EditorState } from "@codemirror/state";
import {
  skillCompletion,
  skillReferences,
  skillSpans,
  retainedSkillSpans,
  type SkillCatalog,
} from "./skill-references";

export const catalog: SkillCatalog = {
  catalog_id: "a".repeat(64),
  context_kind: "draft",
  items: [
    {
      item_id: "b".repeat(64),
      name: "review",
      description: "Review code\n carefully.",
      source_id: "project",
      logical_path: ".agents/skills/review",
    },
  ],
};
it("matches TUI dollar names exactly, deduplicates, and leaves unknown text alone", () => {
  expect(
    skillReferences(
      ["Use $review $unknown $review\n$review, x$review"],
      catalog,
    ),
  ).toEqual([
    {
      catalog_id: catalog.catalog_id,
      item_id: catalog.items[0].item_id,
      name: "review",
    },
  ]);
  expect(
    skillReferences(["$rev", { attachment_id: "a" }, "iew"], catalog),
  ).toHaveLength(0);
  expect(skillReferences(["$unknown x$review $review,"], catalog)).toEqual([]);
});
it("retains Unicode code-point occurrences and ignores missing or mismatched historical metadata", () => {
  const text = "中文😀 $review $review";
  const spans = skillSpans(text, catalog);
  expect(spans).toEqual([
    { name: "review", source_id: "project", start: 4, end: 11 },
    { name: "review", source_id: "project", start: 12, end: 19 },
  ]);
  expect(retainedSkillSpans(text, { skills: spans })).toEqual(spans);
  expect(retainedSkillSpans(text, {})).toEqual([]);
  expect(retainedSkillSpans("changed", { skills: spans })).toEqual([]);
  expect(retainedSkillSpans(text, { skills: spans, long_text: true })).toEqual(
    [],
  );
});
it("offers prefix completion with descriptions only at dollar token boundaries", async () => {
  const source = skillCompletion(async () => catalog);
  const complete = (doc: string) =>
    source(
      new CompletionContext(EditorState.create({ doc }), doc.length, false),
    );
  expect((await complete("Use $rev"))?.options).toEqual([
    {
      label: "$review",
      detail: "Review code carefully.",
      apply: "$review ",
      type: "text",
    },
  ]);
  expect(await complete("x$rev")).toBeNull();
  expect(await complete("/review")).toBeNull();
  expect((await complete("$unknown"))?.options).toEqual([]);
});

it("reuses the full catalog when a prefix narrows, broadens, or has no matches", async () => {
  const context = (doc: string) =>
    new CompletionContext(EditorState.create({ doc }), doc.length, false);
  const initial = await skillCompletion(async () => catalog)(context("$rev"));
  expect(initial?.update).toBeTypeOf("function");
  const update = (doc: string) =>
    initial!.update!(initial!, 0, doc.length, context(doc));
  expect(update("$review")?.options).toHaveLength(1);
  expect(update("$REVIEW")?.options).toEqual([]);
  expect(update("$rvw")?.options).toEqual([]);
  expect(update("$unknown")?.options).toEqual([]);
  expect(update("$")?.options).toHaveLength(1);
  expect(update("x$review")).toBeNull();
  expect(update("$review ")).toBeNull();
  expect(update("")).toBeNull();
});
