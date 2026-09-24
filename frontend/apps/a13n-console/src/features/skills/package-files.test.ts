import { strToU8, zipSync } from "fflate";
import { expect, it } from "vitest";
import { previewLimit, readTextFile } from "./package-files";

const file = (path: string, size = 0) => ({ path, size });

it("reads only the selected file below the package root and preserves UTF-8 source", () => {
  const text = "---\nname: 示例\ndescription: Review\n---\n# Review\n";
  const bytes = strToU8(text);
  const archive = zipSync({
    "SKILL.md": bytes,
    "reference.txt": strToU8("other"),
  });
  expect(readTextFile(archive, "", file("SKILL.md", bytes.length))).toBe(text);
  const nested = zipSync({ "review/SKILL.md": bytes });
  expect(readTextFile(nested, "review/", file("SKILL.md", bytes.length))).toBe(
    text,
  );
});

it("does not render binary, invalid UTF-8, or oversized files as text", () => {
  const archive = zipSync({
    binary: new Uint8Array([0, 1]),
    invalid: new Uint8Array([255]),
    empty: new Uint8Array(),
  });
  expect(readTextFile(archive, "", file("binary", 2))).toBeNull();
  expect(readTextFile(archive, "", file("invalid", 1))).toBeNull();
  expect(readTextFile(archive, "", file("large", previewLimit + 1))).toBeNull();
  expect(readTextFile(archive, "", file("empty"))).toBe("");
  expect(() => readTextFile(archive, "", file("missing"))).toThrow();
  expect(() => readTextFile(archive, "", file("binary", 1))).toThrow();
});
