import { expect, it, vi } from "vitest";
import { createTransport } from "../transport/client";
import { CompletionContext } from "@codemirror/autocomplete";
import { EditorState } from "@codemirror/state";
import {
  loadThreadSkills,
  skillCompletion,
  skillReferences,
  skillSpans,
  retainedSkillSpans,
  type SkillCatalog,
} from "./skill-references";

vi.mock("../platform", () => ({ apiOrigin: () => "http://localhost" }));

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
it("forwards every Environment preview axis, including an explicit cleared default", async () => {
  const transport = createTransport("fixture", () => {});
  const post = vi
    .spyOn(transport.client, "POST")
    .mockResolvedValue({ data: catalog, response: new Response() });
  const get = vi
    .spyOn(transport.client, "GET")
    .mockResolvedValue({ data: catalog, response: new Response() });
  const environment = {
    local_roots: [],
    environment_profile_id: "environment-envd",
    environment_bindings: [],
    default_environment: null,
  };
  const signal = new AbortController().signal;
  try {
    expect(
      await loadThreadSkills(transport, "thread-preview", environment, signal),
    ).toBe(catalog);
    expect(post).toHaveBeenCalledWith("/api/threads/{thread_id}/skills", {
      params: { path: { thread_id: "thread-preview" } },
      body: environment,
      signal,
    });
    expect(get).not.toHaveBeenCalled();
    await loadThreadSkills(transport, "thread-preview");
    expect(get).toHaveBeenCalledOnce();
  } finally {
    transport.close();
  }
});
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
