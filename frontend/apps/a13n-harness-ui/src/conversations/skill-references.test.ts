import { expect, it } from "vitest";
import { CompletionContext } from "@codemirror/autocomplete";
import { EditorState } from "@codemirror/state";
import {
  skillCompletion,
  skillReferences,
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
  ).toHaveLength(1);
  expect(skillReferences(["$unknown x$review $review,"], catalog)).toEqual([]);
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
