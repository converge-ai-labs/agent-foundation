import { expect, it, vi } from "vitest";
import { createClient, type ThreadDelta } from "../../service-client";
import { fixtureRun } from "./transcript/fixture";
import {
  applyDelta,
  comparePositions,
  isOmitted,
  readDisplay,
  type DisplayItem,
} from "./display";

it("reads the committed display of one Run", async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    expect(new URL((input as Request).url).pathname).toBe(
      "/api/v1/runs/run_2/items",
    );
    return Response.json({
      run: fixtureRun(),
      items: [],
      position: "1-4",
      complete: true,
    });
  });
  const client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch,
  });
  expect(
    await readDisplay(client, "ws_1", "run_2", new AbortController().signal),
  ).toMatchObject({ position: "1-4", complete: true });
});

it("orders display positions numerically, at any size", () => {
  expect(comparePositions("100-10", "100-2")).toBeGreaterThan(0);
  expect(comparePositions("2-1", "10-0")).toBeLessThan(0);
  expect(comparePositions("3-7", "3-7")).toBe(0);
  expect(
    comparePositions("9007199254740993-0", "9007199254740992-0"),
  ).toBeGreaterThan(0);
  expect(() => comparePositions("1-01", "1-2")).toThrow(
    "Invalid display position.",
  );
  expect(() => comparePositions("1", "1-2")).toThrow();
});

const item: DisplayItem = {
  id: "message",
  ordinal: 1,
  kind: "text_message",
  state: "in_progress",
  first_stream_id: "1-1",
  last_stream_id: "1-1",
  started_at: "2026-09-20T10:00:00.000Z",
  ended_at: null,
  content: { messageId: "m", role: "assistant", text: "Hel" },
};
const delta = (
  sequence: number,
  changes: ThreadDelta["changes"],
): ThreadDelta => ({ run_id: "run_2", attempt: 1, sequence, changes });
const append = (
  sequence: number,
  text: string,
  id = item.id,
): ThreadDelta["changes"][number] => ({
  type: "append",
  id,
  field: "text",
  text,
  last_stream_id: `1-${sequence}`,
  state: "in_progress",
  ended_at: null,
});

it("applies typed changes and deduplicates the overlap with a committed baseline", () => {
  const items = new Map<string, DisplayItem>();
  expect(applyDelta(items, delta(1, [{ type: "set", item }]))).toBe(true);
  expect(applyDelta(items, delta(1, [append(1, "Hel")]))).toBe(true);
  expect(applyDelta(items, delta(2, [append(2, "lo")]))).toBe(true);
  expect(items.get(item.id)?.content.text).toBe("Hello");
  expect(item.content.text).toBe("Hel");
  expect(applyDelta(items, delta(2, [append(2, "lo")]))).toBe(true);
  expect(items.get(item.id)?.content.text).toBe("Hello");
});

it("does not partially apply a batch whose append baseline is missing", () => {
  const items = new Map<string, DisplayItem>();
  expect(
    applyDelta(
      items,
      delta(2, [{ type: "set", item }, append(2, "lost", "missing")]),
    ),
  ).toBe(false);
  expect(items.size).toBe(0);
});

it("keeps server-owned observation and tool completion semantics without reinterpreting them", () => {
  const items = new Map<string, DisplayItem>();
  const call: DisplayItem = {
    ...item,
    id: "call",
    kind: "tool_call",
    content: { toolCallId: "c", arguments: "{}" },
  };
  const observation: DisplayItem = {
    ...item,
    id: "evidence",
    ordinal: 2,
    kind: "observation",
    state: "completed",
    content: { name: "plugin.large", value: { omitted: true } },
  };
  applyDelta(
    items,
    delta(1, [
      { type: "set", item: call },
      { type: "set", item: observation },
    ]),
  );
  expect(items.get("call")?.state).toBe("in_progress");
  applyDelta(
    items,
    delta(2, [
      {
        type: "set",
        item: { ...call, state: "failed", last_stream_id: "1-2" },
      },
    ]),
  );
  expect(items.get("call")?.state).toBe("failed");
  expect(items.get("evidence")).toEqual(observation);
});

it("recognizes content the display omitted over its limit", () => {
  expect(isOmitted({ omitted: true })).toBe(true);
  expect(isOmitted({ omitted: true, text: "kept" })).toBe(false);
  expect(isOmitted({})).toBe(false);
});
