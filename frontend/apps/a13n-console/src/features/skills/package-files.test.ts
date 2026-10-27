import { strToU8, zipSync } from "fflate";
import { expect, it } from "vitest";
import { linkedPackageFile, previewLimit, readTextFile } from "./package-files";

const file = (path: string, size = 0) => ({ path, size });

it("resolves package links relative to the selected file without intercepting external or unknown links", () => {
  const files = [
    file("SKILL.md"),
    file("references/guide.md"),
    file("references/with space.md"),
  ];
  expect(
    linkedPackageFile("SKILL.md", "references/guide.md#steps", files),
  ).toBe("references/guide.md");
  expect(linkedPackageFile("references/guide.md", "../SKILL.md", files)).toBe(
    "SKILL.md",
  );
  expect(
    linkedPackageFile("SKILL.md", "references/with%20space.md", files),
  ).toBe("references/with space.md");
  for (const href of [
    "https://example.com/SKILL.md",
    "//example.com/SKILL.md",
    "/SKILL.md",
    "#heading",
    "mailto:user@example.com",
    "missing.md",
    "%invalid",
  ]) {
    expect(linkedPackageFile("SKILL.md", href, files)).toBeUndefined();
  }
});

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
