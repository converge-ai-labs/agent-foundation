import { expect, it, vi } from "vitest";
import { type DisplayChange, isDisplayChange } from "a13n-ui/display";
import { createClient, type ThreadDelta } from "../../service-client";
import { fixtureRun } from "./transcript/fixture";
import {
  applyDelta,
  comparePositions,
  isOmitted,
  readDisplay,
  type DisplayItem,
} from "./display";
import { RunDisplayState } from "./run-display-state";

const item = (sequence = 1, text = "Hello"): DisplayItem => ({
  id: "message",
  ordinal: 1,
  kind: "text_message",
  state: "in_progress",
  first_stream_id: "1-1",
  last_stream_id: `1-${sequence}`,
  started_at: "2026-09-20T10:00:00Z",
  ended_at: null,
  content: { text, role: "assistant", messageId: "m" },
});
const delta = (sequence: number, changes: DisplayChange[]): ThreadDelta => ({
  run_id: "run_2",
  attempt: 1,
  sequence,
  format: "display-ops-v1",
  changes,
});
const append = (sequence: number, text = "!"): DisplayChange => ({
  type: "append",
  id: "message",
  field: "text",
  text,
  after_stream_id: `1-${sequence - 1}`,
  last_stream_id: `1-${sequence}`,
  state: "in_progress",
  ended_at: null,
});
const baseline = (sequence: number, complete = false) => ({
  run: fixtureRun(),
  items: [item(sequence)],
  position: `1-${sequence}`,
  complete,
});

it("reads the committed display of one Run", async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    expect(new URL((input as Request).url).pathname).toBe(
      "/api/v1/runs/run_2/items",
    );
    return Response.json(baseline(4, true));
  });
  const client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch,
  });
  expect(await readDisplay(client, "ws_1", "run_2")).toMatchObject({
    position: "1-4",
    complete: true,
  });
});

it("orders decimal positions without precision loss", () => {
  expect(comparePositions("100-10", "100-2")).toBeGreaterThan(0);
  expect(comparePositions("2-1", "10-0")).toBeLessThan(0);
  expect(comparePositions("3-7", "3-7")).toBe(0);
  expect(
    comparePositions("9007199254740993-0", "9007199254740992-0"),
  ).toBeGreaterThan(0);
  expect(() => comparePositions("1-01", "1-2")).toThrow();
});

it("applies batches atomically and keeps opaque media/App metadata intact", () => {
  const original = item();
  original.content.result_parts = [
    { type: "image", url: "https://example.test/image" },
  ];
  original.content.metadata = { apps: [{ snapshot_id: "app_one" }] };
  const items = new Map<string, DisplayItem>();
  applyDelta(items, delta(1, [{ type: "set", item: original }]));
  applyDelta(items, delta(2, [append(2)]));
  expect(items.get("message")?.content).toEqual({
    ...original.content,
    text: "Hello!",
  });
  const before = structuredClone(items);
  expect(() =>
    applyDelta(
      items,
      delta(3, [
        { type: "set", item: { ...item(), id: "new" } },
        { ...append(3), id: "missing" } as DisplayChange,
      ]),
    ),
  ).toThrow();
  expect(items).toEqual(before);
  expect(() => applyDelta(items, delta(2, [append(2)]))).toThrow();
});

it("validates operation shape rather than accepting a raw-event payload", () => {
  expect(isDisplayChange({ type: "set", item: item() })).toBe(true);
  expect(isDisplayChange(append(2))).toBe(true);
  expect(isDisplayChange({ type: "set", item: { id: "partial" } })).toBe(false);
  expect(isDisplayChange({ type: "TEXT_MESSAGE_CONTENT", delta: "x" })).toBe(
    false,
  );
  expect(isOmitted({ omitted: true })).toBe(true);
  expect(isOmitted({ omitted: true, text: "kept" })).toBe(false);
});

it("rejects stale durable baselines but replays a valid newer baseline's suffix", () => {
  const state = new RunDisplayState();
  state.reconcile(baseline(5), 1);
  state.receive(delta(6, [append(6)]), "6-0");
  expect(state.reconcile(baseline(3), 1)).toBe(false);
  expect(state.position).toBe("1-6");
  expect(state.items.get("message")?.content.text).toBe("Hello!");
  state.receive(delta(7, [append(7)]), "7-0");
  state.reconcile(baseline(6), 1);
  expect(state.position).toBe("1-7");
  expect(state.items.get("message")?.content.text).toBe("Hello!");
});

it("refreshes covered boundaries and releases acknowledged pending batches", () => {
  const state = new RunDisplayState();
  state.reconcile(baseline(1), 1);
  for (let sequence = 2; sequence < 2100; sequence++) {
    state.receive(delta(sequence, [append(sequence)]), `${sequence}-0`);
    if (sequence % 50 === 0) {
      expect(state.boundary(1, sequence, `${sequence}-1`)).toBe(true);
      state.reconcile(baseline(sequence), 1);
    }
  }
  expect(state.incomplete).toBe(false);
});

it.each(["count", "bytes"])(
  "requires durable coverage after pending %s overflow",
  (limit) => {
    const state = new RunDisplayState();
    state.reconcile(baseline(1), 1);
    const end = limit === "count" ? 1030 : 3;
    for (let sequence = 2; sequence <= end; sequence++)
      state.receive(
        delta(sequence, [
          append(sequence, limit === "bytes" ? "x".repeat(600000) : "x"),
        ]),
        `${sequence}-0`,
      );
    expect(state.incomplete).toBe(true);
    state.reconcile(baseline(2), 1);
    expect(state.incomplete).toBe(true);
    state.reconcile(baseline(end), 1);
    expect(state.incomplete).toBe(false);
  },
);

it("does not let an old queued frame clear unknown transport loss", () => {
  const state = new RunDisplayState();
  state.reconcile(baseline(1), 1);
  state.gap();
  state.receive(delta(2, [append(2)]), "2-0");
  expect(state.incomplete).toBe(true);
  state.reconcile(baseline(1), 1);
  expect(state.incomplete).toBe(true);
  state.boundary(1, 2, "2-1");
  state.reconcile(baseline(2), 1);
  expect(state.incomplete).toBe(false);
});

it("does not advance coverage when any operation fails", () => {
  const state = new RunDisplayState();
  state.reconcile(baseline(1), 1);
  state.receive(
    delta(2, [{ ...append(2), after_stream_id: "1-0" } as DisplayChange]),
    "2-0",
  );
  expect(state.position).toBe("1-1");
  expect(state.incomplete).toBe(true);
});

it("accepts a shorter terminal baseline and discards provisional output on a new attempt", () => {
  const state = new RunDisplayState();
  state.reconcile(baseline(5), 1);
  state.receive(delta(6, [append(6)]), "6-0");
  state.reconcile(baseline(3, true), 1);
  expect(state.position).toBe("1-3");
  expect(state.items.get("message")?.content.text).toBe("Hello");
  const active = new RunDisplayState();
  active.reconcile(baseline(1), 1);
  active.receive(delta(2, [append(2)]), "2-0");
  active.receive({ ...delta(1, []), attempt: 2 }, "3-0");
  expect(active.position).toBe("2-1");
  expect(active.items.get("message")?.content.text).toBe("Hello");
});
