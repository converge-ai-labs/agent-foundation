import { expect, it, vi } from "vitest";
import { data, representation, matchesSearch, matchingPage } from "./api";
import { data as clientData, ProtocolError } from "../service-client";

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

it("keeps full response data and its original ETag for editing", () => {
  const value = {
    id: "revision",
    config: { instructions: "keep", toolsets: {} },
  };
  const response = new Response(null, { headers: { ETag: '\"agent:4\"' } });
  expect(representation({ data: value, response })).toEqual({
    value,
    etag: '\"agent:4\"',
  });
  expect(
    data({ data: { items: [value], next_cursor: null }, response }),
  ).toEqual({
    items: [value],
    next_cursor: null,
  });
});

it.each([
  { view: "compact", id: "revision", config: { model: "model" } },
  { representation: "fragment", text: '{"config":' },
  { items: [{ view: "compact", id: "revision" }], next_cursor: null },
  { view: "detailed", items: [], run: { view: "compact" } },
])(
  "rejects a partial response before it enters an editor or full display",
  (value) => {
    const result = { data: value, response: new Response() };
    expect(() => data(result)).toThrow(ProtocolError);
    expect(() => representation(result)).toThrow("complete detailed response");
    expect(clientData(result)).toBe(value);
  },
);

it("preserves the missing-representation error", () => {
  expect(() => data({ response: new Response() })).toThrow(
    "The Service returned no representation.",
  );
});
