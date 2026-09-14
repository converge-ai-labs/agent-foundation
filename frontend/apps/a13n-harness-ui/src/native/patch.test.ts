import { expect, it } from "vitest";
import { patchLines } from "./patch";

it("annotates exact patch lines without treating headers or no-newline notices as file lines", () => {
  const patch =
    "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -10,3 +20,3 @@ function\n context\n--- deleted prefix\n\\ No newline at end of file\n+++ added prefix\n last\n";
  expect(patchLines(patch)).toEqual([
    { kind: "metadata" },
    { kind: "metadata" },
    { kind: "metadata" },
    { kind: "hunk" },
    { kind: "context", oldLine: 10, newLine: 20 },
    { kind: "deletion", oldLine: 11 },
    { kind: "metadata" },
    { kind: "addition", newLine: 21 },
    { kind: "context", oldLine: 12, newLine: 22 },
    { kind: "metadata" },
  ]);
});
it("handles zero-length sides, multiple hunks, CRLF and omitted single-line counts", () => {
  expect(
    patchLines(
      "@@ -0,0 +1,2 @@\r\n+first\r\n+second\r\n@@ -8 +10,0 @@\r\n-old\r\n",
    ),
  ).toEqual([
    { kind: "hunk" },
    { kind: "addition", newLine: 1 },
    { kind: "addition", newLine: 2 },
    { kind: "hunk" },
    { kind: "deletion", oldLine: 8 },
    { kind: "metadata" },
  ]);
});
it("leaves unknown and combined comparisons readable without fabricating ordinary file gutters", () => {
  expect(
    patchLines("@@@ -1,1 -1,1 +1,1 @@@\n++combined\nBinary files differ").every(
      (line) => line.kind === "metadata",
    ),
  ).toBe(true);
});
