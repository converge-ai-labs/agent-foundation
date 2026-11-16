import { expect, it } from "vitest";
import { workspaceDocumentTitle } from "./workspace-title";

it.each(["New chat", "排查标题显示问题", "a".repeat(80)])(
  "keeps short document titles unchanged: %s",
  (title) => {
    expect(workspaceDocumentTitle(title)).toBe(`${title} - Harness UI`);
  },
);

it.each(["a", "字", "👩🏽‍💻", "e\u0301"])(
  "bounds long document titles without splitting graphemes: %s",
  (grapheme) => {
    expect(workspaceDocumentTitle(grapheme.repeat(81))).toBe(
      `${grapheme.repeat(79)}… - Harness UI`,
    );
  },
);

it("does not leave whitespace before the ellipsis", () => {
  expect(workspaceDocumentTitle(`${"a".repeat(78)} ${"b".repeat(10)}`)).toBe(
    `${"a".repeat(78)}… - Harness UI`,
  );
});
