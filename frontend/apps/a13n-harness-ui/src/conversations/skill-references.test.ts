import { expect, it, vi } from "vitest";
import { createTransport } from "../transport/client";
import { CompletionContext } from "@codemirror/autocomplete";
import { EditorState } from "@codemirror/state";
import {
  hasSkillTrigger,
  leadingSlashSkillToken,
  loadThreadSkills,
  skillCompletion,
  skillReferences,
  skillSourceLabel,
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
    await loadThreadSkills(transport, "thread-preview", null, signal, true);
    expect(get).toHaveBeenLastCalledWith("/api/threads/{thread_id}/skills", {
      params: {
        path: { thread_id: "thread-preview" },
        query: { coordinator: true },
      },
      signal,
    });
    await loadThreadSkills(
      transport,
      "thread-preview",
      environment,
      signal,
      true,
    );
    expect(post).toHaveBeenLastCalledWith("/api/threads/{thread_id}/skills", {
      params: {
        path: { thread_id: "thread-preview" },
        query: { coordinator: true },
      },
      body: environment,
      signal,
    });
  } finally {
    transport.close();
  }
});
it("labels built-in coordinator skills separately from ordinary built-in skills", () => {
  expect(skillSourceLabel("a13n-harness-ui:builtin-skills")).toBe("Built-in");
  expect(skillSourceLabel("a13n-harness-ui:builtin-coordinator-skills")).toBe(
    "Coordinator",
  );
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
it("offers prefix completion with descriptions at dollar and slash token boundaries", async () => {
  const source = skillCompletion(async () => catalog);
  const complete = (doc: string) =>
    source(
      new CompletionContext(EditorState.create({ doc }), doc.length, false),
    );
  expect((await complete("Use $rev"))?.options).toMatchObject([
    {
      label: "$review",
      detail: "Review code carefully.",
      apply: "$review ",
      displayLabel: "Review",
    },
  ]);
  expect(await complete("x$rev")).toBeNull();
  expect((await complete("/review"))?.options[0]?.apply).toBe("$review ");
  expect((await complete("$review /rev"))?.options[0]?.apply).toBe("$review ");
  expect((await complete("$review $rev"))?.options[0]?.apply).toBe("$review ");
  expect(await complete("/review/file")).toBeNull();
  expect(await complete("https://review")).toBeNull();
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

it("recognizes direct leading /skill-name references while preserving peer commands and paths", () => {
  const excluded = new Set(["compact", "plan"]);
  expect(leadingSlashSkillToken("/review fix bug")).toEqual({
    name: "review",
    start: 0,
    end: 7,
  });
  expect(leadingSlashSkillToken("/usr/bin/env")).toBeNull();
  expect(hasSkillTrigger(["/review fix bug"], excluded)).toBe(true);
  expect(hasSkillTrigger(["/compact now"], excluded)).toBe(false);
  expect(hasSkillTrigger(["check /review"], excluded)).toBe(false);
  expect(hasSkillTrigger(["check $review"], excluded)).toBe(true);

  expect(
    skillReferences(["/review fix bug"], catalog, {
      excludedSlashNames: excluded,
    }),
  ).toEqual([
    {
      catalog_id: catalog.catalog_id,
      item_id: catalog.items[0].item_id,
      name: "review",
    },
  ]);
  expect(
    skillReferences(["/compact now"], catalog, {
      excludedSlashNames: excluded,
    }),
  ).toEqual([]);
  expect(
    skillReferences(["/unknown do thing"], catalog, {
      excludedSlashNames: excluded,
    }),
  ).toEqual([]);
  expect(
    skillReferences(["check /review"], catalog, {
      excludedSlashNames: excluded,
    }),
  ).toEqual([]);
  const slashSpans = skillSpans("/review and $review", catalog, {
    allowLeadingSlash: true,
    excludedSlashNames: excluded,
  });
  expect(slashSpans).toEqual([
    { name: "review", source_id: "project", start: 0, end: 7 },
    { name: "review", source_id: "project", start: 12, end: 19 },
  ]);
  expect(
    retainedSkillSpans("/review and $review", { skills: slashSpans }),
  ).toEqual(slashSpans);
  expect(
    retainedSkillSpans("check /review", {
      skills: [{ name: "review", source_id: "project", start: 6, end: 13 }],
    }),
  ).toEqual([]);
  expect(skillSourceLabel("acp-command")).toBe("Command");
});

it("disambiguates duplicate skill names when slash completion merges peer commands", async () => {
  const source = skillCompletion(async () => catalog, {
    commandNames: () => new Set(["review"]),
  });
  const slash = await source(
    new CompletionContext(EditorState.create({ doc: "/rev" }), 4, false),
  );
  expect(slash?.options).toMatchObject([
    {
      label: "$review",
      displayLabel: "Review ($review)",
      apply: "$review ",
    },
  ]);
  const dollar = await source(
    new CompletionContext(EditorState.create({ doc: "$rev" }), 4, false),
  );
  expect(dollar?.options).toMatchObject([
    {
      label: "$review",
      displayLabel: "Review",
      apply: "$review ",
    },
  ]);
});
