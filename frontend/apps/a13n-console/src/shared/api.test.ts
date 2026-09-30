import { expect, it, vi } from "vitest";
import { matchesSearch, matchingPage } from "./api";

const pages: Record<string, { items: string[]; next_cursor: string | null }> = {
  first: { items: ["Alpha", "Beta"], next_cursor: "second" },
  second: { items: ["Alphabet"], next_cursor: null },
};

it("keeps the requested page while nothing is matched", async () => {
  const read = vi.fn(async (cursor = "first") => pages[cursor]);
  expect(await matchingPage(read, "second")).toEqual(pages.second);
  expect(read).toHaveBeenCalledOnce();
});

it("matches across the whole collection, never only the current page", async () => {
  const read = vi.fn(async (cursor = "first") => pages[cursor]);
  const page = await matchingPage(read, "second", (item) =>
    matchesSearch("alp", item),
  );
  expect(page).toEqual({ items: ["Alpha", "Alphabet"], next_cursor: null });
  expect(read).toHaveBeenCalledWith(undefined, 100);
  expect(read).toHaveBeenCalledWith("second", 100);
});

it("matches any of the texts, ignoring missing ones", () => {
  expect(matchesSearch("", undefined)).toBe(true);
  expect(matchesSearch("box", null, "Build box")).toBe(true);
  expect(matchesSearch("box", "Build", undefined)).toBe(false);
});
