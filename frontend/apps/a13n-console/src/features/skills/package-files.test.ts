import { strToU8, zipSync } from "fflate";
import { expect, it } from "vitest";
import {
  fileTree,
  markdownBody,
  previewLimit,
  readTextFile,
} from "./package-files";

const file = (path: string, size_bytes = 0) => ({
  path,
  size_bytes,
  sha256: "fixture",
});

it("keeps nested paths distinct and sorts directories before files", () => {
  const tree = fileTree([
    file("SKILL.md"),
    file("references/check.md"),
    file("scripts/check.md"),
    file("references/deep/example.txt"),
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

it("reads only the selected file and preserves UTF-8 source", () => {
  const text = "---\nname: 示例\ndescription: Review\n---\n# Review\n";
  const bytes = strToU8(text);
  const archive = zipSync({
    "SKILL.md": bytes,
    "reference.txt": strToU8("other"),
  });
  expect(readTextFile(archive, file("SKILL.md", bytes.length))).toBe(text);
  expect(markdownBody(text)).toBe("# Review\n");
  expect(markdownBody("---\nordinary markdown")).toBe("---\nordinary markdown");
});

it("does not render binary, invalid UTF-8, or oversized files as text", () => {
  const archive = zipSync({
    binary: new Uint8Array([0, 1]),
    invalid: new Uint8Array([255]),
    empty: new Uint8Array(),
  });
  expect(readTextFile(archive, file("binary", 2))).toBeNull();
  expect(readTextFile(archive, file("invalid", 1))).toBeNull();
  expect(readTextFile(archive, file("large", previewLimit + 1))).toBeNull();
  expect(readTextFile(archive, file("empty"))).toBe("");
  expect(() => readTextFile(archive, file("missing"))).toThrow();
  expect(() => readTextFile(archive, file("binary", 1))).toThrow();
});
