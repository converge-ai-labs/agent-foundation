import { expect, it } from "vitest";
import { canHighlightContent } from "./content-budget";

it("budgets all retained content, including nested messages and object keys", () => {
  expect(canHighlightContent("a".repeat(100_000))).toBe(true);
  expect(canHighlightContent("a".repeat(100_001))).toBe(false);
  expect(
    canHighlightContent({
      messages: [{ role: "user", content: "Hello" }],
      count: 3,
      done: false,
      extra: null,
    }),
  ).toBe(true);
  const messages = Array.from({ length: 100 }, () => ({
    role: "user",
    content: "a".repeat(1_000),
  }));
  expect(canHighlightContent({ messages })).toBe(false);
  expect(canHighlightContent(JSON.stringify({ messages }))).toBe(false);
  expect(canHighlightContent({ ["a".repeat(100_001)]: null })).toBe(false);
});

it("uses plain rendering for excessively nested content without walking the full structure", () => {
  let value: unknown = "leaf";
  for (let depth = 0; depth < 10_000; depth++) value = { child: value };
  expect(canHighlightContent(value)).toBe(false);
});
