import { expect, it, vi } from "vitest";
import { createClient } from "../../service-client";
import { readDisplay } from "./display";

const item = (id: string) => ({
  id,
  kind: "text_message",
  state: "in_progress",
  first_stream_id: "1-0",
  last_stream_id: "1-0",
  parent_item_id: null,
  content: { text: id },
});
const page = (
  version: number,
  items: ReturnType<typeof item>[],
  next: string | null,
) => ({
  items,
  next_cursor: next,
  snapshot_version: version,
  projection_cursor: `${version}-0`,
  complete: true,
  incomplete_reason: null,
  finalized: false,
});

it("reads only the latest page and presents it in chronological order", async () => {
  const fetch = vi.fn(async (input) => {
    const query = new URL((input as Request).url).searchParams;
    expect(query.get("order")).toBe("desc");
    expect(query.get("limit")).toBe("50");
    expect(query.has("cursor")).toBe(false);
    return Response.json(page(1, [item("newest"), item("older")], "earlier"));
  });
  const client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch,
  });
  const display = await readDisplay(
    client,
    "workspace",
    "run",
    new AbortController().signal,
  );
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(display).toMatchObject({
    available: true,
    next_cursor: "earlier",
    projection_cursor: "1-0",
  });
  expect(display.items.map((value) => value.id)).toEqual(["older", "newest"]);
});

it("loads a requested earlier page even when the live snapshot has advanced", async () => {
  const fetch = vi.fn(async (input) => {
    expect(new URL((input as Request).url).searchParams.get("cursor")).toBe(
      "earlier",
    );
    return Response.json(page(2, [item("oldest")], null));
  });
  const client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch,
  });
  const display = await readDisplay(
    client,
    "workspace",
    "run",
    new AbortController().signal,
    "earlier",
  );
  expect(display).toMatchObject({
    available: true,
    next_cursor: null,
    snapshot_version: 2,
  });
  expect(fetch).toHaveBeenCalledTimes(1);
});
