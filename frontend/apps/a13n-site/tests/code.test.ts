import { expect, test } from "vitest";
import { charsOf } from "../src/scene/code";

test("charsOf marks the requested text and preserves whitespace", () => {
  const [line] = charsOf(['start(⟨"Hi"⟩)']);
  expect(line.map((c) => c.ch).join("")).toBe('start("Hi")');
  expect(
    line
      .filter((c) => c.key)
      .map((c) => c.ch)
      .join(""),
  ).toBe('"Hi"');
  expect(line.find((c) => c.ch === '"')?.tone).toBe("str");
  expect(
    charsOf(["\ta b"])[0]
      .map((c) => c.ch)
      .join(""),
  ).toBe("\u00a0".repeat(4) + "a\u00a0b");
});
