// @vitest-environment node
import { expect, it } from "vitest";
import { diffSections } from "./lines";

it("removes transport headers and aligns replacements with numbered context", () => {
  const [section] = diffSections([
    "--- baseline/test\n+++ candidate/test\n@@ -4,3 +4,4 @@\n heading\n-old decision\n+new decision\n+another line\n tail\n",
  ]);
  expect(section.before).toBe(4);
  expect(section.rows).toHaveLength(4);
  expect(section.rows[0]).toMatchObject({
    before: { number: 4, text: "heading" },
    after: { number: 4, text: "heading" },
    context: true,
  });
  expect(section.rows[1].before?.parts).toContainEqual({
    text: "old",
    changed: true,
  });
  expect(section.rows[1].after?.parts).toContainEqual({
    text: "new",
    changed: true,
  });
  expect(section.rows[2]).toEqual({
    before: undefined,
    after: { number: 6, text: "another line" },
  });
  expect(section.rows[3].before?.number).toBe(6);
  expect(section.rows[3].after?.number).toBe(7);
});

it("preserves blank content, whitespace changes and final-newline markers", () => {
  const [section] = diffSections([
    "@@ -1,2 +1,2 @@\n-\n-old\n\\ No newline at end of file\n+ \n+old\n",
  ]);
  expect(section.rows[0].before?.text).toBe("");
  expect(section.rows[0].after?.parts).toEqual([{ text: " ", changed: true }]);
  expect(section.rows[1].before?.noNewline).toBe(true);
  expect(section.rows[1].after?.noNewline).toBeUndefined();
});

it("reads additions, deletions, header-like file content and independent hunks", () => {
  const sections = diffSections([
    "@@ -0,0 +1,2 @@\n+++ literal\n+",
    "@@ -8 +0,0 @@\n--- literal",
  ]);
  expect(sections[0].rows.map((row) => row.after?.text)).toEqual([
    "++ literal",
    "",
  ]);
  expect(sections[0].rows[0].before).toBeUndefined();
  expect(sections[1].rows[0].before).toEqual({ number: 8, text: "-- literal" });
  expect(sections[1].rows[0].after).toBeUndefined();
});

it("keeps bounded incomplete hunks without inventing missing records", () => {
  const sections = diffSections([
    "--- before\n+++ after\n@@ -1,100 +1,100 @@\n-one\n+tw",
  ]);
  expect(sections[0].rows).toHaveLength(1);
  expect(sections[0].rows[0].after?.text).toBe("tw");
  expect(diffSections([""])).toEqual([]);
  expect(diffSections(["--- before\n+++ after\n@@ -1"])).toEqual([]);
});

it("separates distant hunks and bounds highlighting of unrelated text", () => {
  const sections = diffSections([
    `@@ -1 +1 @@\n-${"left ".repeat(300)}\n+${"right ".repeat(300)}\n@@ -50 +50 @@\n <script>alert(1)</script>`,
  ]);
  expect(sections).toHaveLength(2);
  expect(sections[0].rows[0].before?.parts).toBeUndefined();
  expect(sections[1].rows[0].before?.text).toBe("<script>alert(1)</script>");
});
