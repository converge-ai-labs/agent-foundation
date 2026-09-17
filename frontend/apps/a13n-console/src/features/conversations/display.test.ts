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

it("discards an obsolete page and restarts against one display version", async () => {
  const responses = [
    Response.json(page(1, [item("old")], "v1-next")),
    Response.json(
      { error: { code: "items_snapshot_changed", message: "Changed" } },
      { status: 409 },
    ),
    Response.json(page(2, [item("new-first")], "v2-next")),
    Response.json(page(2, [item("new-second")], null)),
  ];
  const cursors: (string | null)[] = [];
  const client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch: vi.fn(async (input) => {
      cursors.push(new URL((input as Request).url).searchParams.get("cursor"));
      return responses.shift()!;
    }),
  });
  const display = await readDisplay(
    client,
    "workspace",
    "run",
    new AbortController().signal,
  );
  expect(cursors).toEqual([null, "v1-next", null, "v2-next"]);
  expect(display).toMatchObject({
    available: true,
    snapshot_version: 2,
    projection_cursor: "2-0",
  });
  expect(display.items.map((value) => value.id)).toEqual([
    "new-first",
    "new-second",
  ]);
});

it("rejects pages with inconsistent coverage instead of merging them", async () => {
  const responses = [
    Response.json(page(1, [item("first")], "next")),
    Response.json(page(2, [item("second")], null)),
  ];
  const client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch: vi.fn(async () => responses.shift()!),
  });
  await expect(
    readDisplay(client, "workspace", "run", new AbortController().signal),
  ).rejects.toThrow("inconsistent coverage");
});
