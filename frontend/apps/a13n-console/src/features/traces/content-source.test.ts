import { expect, it } from "vitest";
import { sourceChunks, TEXT_CHUNK_SIZE } from "./content-source";

it("preserves complete source and surrogate pairs across bounded chunks", () => {
  for (const text of [
    "",
    "short",
    "a".repeat(TEXT_CHUNK_SIZE - 1) + "😀tail",
    "line\n".repeat(12_000),
    "x".repeat(100_001),
  ]) {
    const chunks = sourceChunks(text);
    expect(chunks.join("")).toBe(text);
    for (const chunk of chunks) {
      expect(chunk.length).toBeLessThanOrEqual(TEXT_CHUNK_SIZE);
      expect(
        [...chunk].some((character) => /^[\uD800-\uDFFF]$/.test(character)),
      ).toBe(false);
    }
  }
});

it("keeps normal lines together when a nearby newline is available", () => {
  const text = "first line\n".repeat(1_500) + "next line".repeat(1_000);
  const chunks = sourceChunks(text);
  expect(chunks[0]!.endsWith("\n")).toBe(true);
  expect(chunks.join("")).toBe(text);
});
