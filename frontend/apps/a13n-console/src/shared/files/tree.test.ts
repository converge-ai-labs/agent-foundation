import { expect, it } from "vitest";
import { fileTree, isMarkdown, markdownBody } from "./tree";

it("keeps nested paths distinct and sorts directories before files", () => {
  const tree = fileTree([
    { path: "SKILL.md" },
    { path: "references/check.md" },
    { path: "scripts/check.md" },
    { path: "references/deep/example.txt" },
  ]);
  expect(tree.map((node) => node.name)).toEqual([
    "references",
    "scripts",
    "SKILL.md",
  ]);
  expect(tree[0].children?.map((node) => node.path)).toEqual([
    "references/deep",
    "references/check.md",
  ]);
  expect(tree[1].children?.[0].path).toBe("scripts/check.md");
});

it("previews Markdown without its frontmatter", () => {
  expect(markdownBody("---\nname: 示例\n---\n# Review\n")).toBe("# Review\n");
  expect(markdownBody("---\nordinary markdown")).toBe("---\nordinary markdown");
  expect(isMarkdown("notes/README.MD")).toBe(true);
  expect(isMarkdown("notes.txt")).toBe(false);
});
