import { expect, it, vi } from "vitest";
import { DisplayNormalizer, isItemRef } from "a13n-ui/display";
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
const delta = (sequence: number, text = "!"): ThreadDelta => ({
  run_id: "run_2",
  attempt: 1,
  sequence,
  event: { type: "TEXT_MESSAGE_CONTENT", messageId: "m", delta: text },
  item: {
    id: "message",
    kind: "text_message",
    state: "in_progress",
    ordinal: 1,
  },
});
const baseline = (sequence: number, complete = false) => ({
  run: fixtureRun(),
  baseline: true,
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

it("continues normalized items without retransmitting or losing media/App metadata", () => {
  const original = item();
  original.content.result_parts = [
    { type: "image", url: "https://example.test/image" },
  ];
  original.content.metadata = { apps: [{ snapshot_id: "app_one" }] };
  const items = new Map([[original.id, original]]);
  applyDelta(items, delta(2));
  expect(items.get("message")?.content).toEqual({
    ...original.content,
    text: "Hello!",
  });
  expect(isItemRef(delta(2).item)).toBe(true);
  expect(isItemRef({ id: "partial" })).toBe(false);
  expect(isOmitted({ omitted: true })).toBe(true);
  expect(isOmitted({ omitted: true, text: "kept" })).toBe(false);
});

it("rejects stale durable baselines but replays a valid newer baseline's suffix", () => {
  const state = new RunDisplayState();
  state.reconcile(baseline(5), 1);
  state.receive(delta(6), "6-0");
  expect(state.reconcile(baseline(3), 1)).toBe(false);
  expect(state.position).toBe("1-6");
  expect(state.items.get("message")?.content.text).toBe("Hello!");
  state.receive(delta(7), "7-0");
  state.reconcile(baseline(6), 1);
  expect(state.position).toBe("1-7");
  expect(state.items.get("message")?.content.text).toBe("Hello!");
});

it("refreshes covered boundaries and releases acknowledged pending batches", () => {
  const state = new RunDisplayState();
  state.reconcile(baseline(1), 1);
  for (let sequence = 2; sequence < 2100; sequence++) {
    state.receive(delta(sequence), `${sequence}-0`);
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
        delta(sequence, limit === "bytes" ? "x".repeat(600000) : "x"),
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
  state.receive(delta(2), "2-0");
  expect(state.incomplete).toBe(true);
  state.reconcile(baseline(1), 1);
  expect(state.incomplete).toBe(true);
  state.boundary(1, 2, "2-1");
  state.reconcile(baseline(2), 1);
  expect(state.incomplete).toBe(false);
});

it("advances read progress through a gap without claiming the partial text is complete", () => {
  const state = new RunDisplayState();
  state.reconcile(baseline(1), 1);
  state.receive(delta(3, "tail"), "3-0");
  state.receive(
    {
      ...delta(4),
      event: { type: "TEXT_MESSAGE_END", messageId: "m" },
      item: { ...delta(4).item!, state: "completed" },
    },
    "4-0",
  );
  expect(state.position).toBe("1-4");
  expect(state.items.get("message")?.content).toMatchObject({
    text: "Hellotail",
    incomplete: true,
  });
  expect(state.incomplete).toBe(true);
  state.reconcile(baseline(1), 1);
  expect(state.incomplete).toBe(true);
  state.reconcile(baseline(4), 1);
  expect(state.incomplete).toBe(false);
  expect(state.items.get("message")?.content.incomplete).toBeUndefined();
});

it("rejects a regressing terminal baseline and discards provisional output on a new attempt", () => {
  const state = new RunDisplayState();
  state.reconcile(baseline(5), 1);
  state.receive(delta(6), "6-0");
  expect(state.reconcile(baseline(3, true), 1)).toBe(false);
  expect(state.position).toBe("1-6");
  expect(state.items.get("message")?.content.text).toBe("Hello!");
  const active = new RunDisplayState();
  active.reconcile(baseline(1), 1);
  active.receive(delta(2), "2-0");
  active.receive({ ...delta(1), attempt: 2, item: null }, "3-0");
  expect(active.position).toBe("2-1");
  expect(active.items.get("message")?.content.text).toBe("Hello");
});

it("never installs a historical window as live state even if it advertises newer sealed coverage", () => {
  const state = new RunDisplayState();
  state.reconcile(baseline(1), 1);
  state.gap("1-3");
  const held = state.items;
  expect(
    state.reconcile(
      { ...baseline(10, true), baseline: false, resume_after: "10-0" },
      1,
    ),
  ).toBe(false);
  expect(state.items).toBe(held);
  expect(state.position).toBe("1-1");
  expect(state.after).toBeUndefined();
  expect(state.incomplete).toBe(true);
  expect(state.read?.complete).toBe(false);
});

it("normalizes every intact raw suffix identically to the Python producer", async () => {
  const { readFileSync } = await import("node:fs");
  const cases = JSON.parse(
    readFileSync(
      new URL(
        "../../../../../../packages/a13n-stream-protocol/tests/fixtures/display-normalization.json",
        import.meta.url,
      ),
      "utf8",
    ),
  ) as {
    initial: import("a13n-ui/display").DisplayContinuation;
    steps: {
      delta: ThreadDelta;
      item?: DisplayItem;
      continuation: import("a13n-ui/display").DisplayContinuation;
    }[];
  }[];
  const normalized = (items: Map<string, DisplayItem>) =>
    [...items.values()].map((item) => ({
      ...item,
      started_at: new Date(item.started_at).toISOString(),
      ended_at: item.ended_at ? new Date(item.ended_at).toISOString() : null,
    }));
  for (const fixture of cases) {
    const expected = new Map<string, DisplayItem>();
    for (const step of fixture.steps)
      if (step.item) expected.set(step.item.id, step.item);
    for (let cut = 0; cut <= fixture.steps.length; cut++) {
      const items = new Map<string, DisplayItem>();
      for (const step of fixture.steps.slice(0, cut))
        if (step.item) items.set(step.item.id, structuredClone(step.item));
      const normalizer = new DisplayNormalizer(
        cut ? fixture.steps[cut - 1]!.continuation : fixture.initial,
      );
      for (const step of fixture.steps.slice(cut))
        normalizer.apply(items, step.delta);
      expect(
        normalized(items),
        `cut ${cut}, full=${fixture.initial.full_content}`,
      ).toEqual(normalized(expected));
      expect(normalizer.incomplete).toBe(false);
    }
  }
});

it("does not parse a JSON fragment suffix after losing its prefix", () => {
  const normalizer = new DisplayNormalizer();
  const items = new Map<string, DisplayItem>();
  normalizer.apply(items, {
    ...delta(2),
    event: {
      type: "CUSTOM",
      name: "a13n.stream.fragment",
      value: { id: "f", index: 1, count: 2, data: '{"complete":true}' },
    },
    item: { id: "observation", kind: "observation", state: "completed" },
  });
  expect(items.size).toBe(0);
  expect(normalizer.incomplete).toBe(true);
});

it("retains Host-selected grouping and native failure diagnostics with raw events", () => {
  const normalizer = new DisplayNormalizer();
  const items = new Map<string, DisplayItem>();
  normalizer.apply(items, {
    ...delta(1),
    event: {
      type: "CUSTOM",
      name: "a13n.pydantic_ai.function_tool_result",
      value: {
        event: {
          part: {
            part_kind: "retry-prompt",
            tool_call_id: "t",
            content: "denied",
          },
        },
      },
    },
    item: {
      id: "tool",
      kind: "tool_call",
      state: "failed",
      ordinal: 8,
      response_group: "saved-answer",
      failure: { code: "tool_failed", message: "denied" },
    },
  });
  expect(items.get("tool")).toMatchObject({
    ordinal: 8,
    state: "failed",
    content: {
      responseGroup: "saved-answer",
      failure: { code: "tool_failed", message: "denied" },
      retry: true,
    },
  });
});

it("keeps orphan tool arguments incomplete after END even if the suffix is valid JSON", async () => {
  const { presentItem, parseItemValue } = await import("./projection");
  const state = new RunDisplayState();
  state.reconcile({ ...baseline(1), items: [] }, 1);
  const ref = {
    id: "tool",
    kind: "tool_call" as const,
    state: "in_progress" as const,
  };
  state.receive(
    {
      ...delta(3),
      item: ref,
      event: { type: "TOOL_CALL_ARGS", toolCallId: "t", delta: "{}" },
    },
    "3-0",
  );
  state.receive(
    {
      ...delta(4),
      item: ref,
      event: { type: "TOOL_CALL_END", toolCallId: "t" },
    },
    "4-0",
  );
  const tool = state.items.get("tool")!;
  expect(tool.content).toMatchObject({
    arguments: "{}",
    incomplete: true,
    arguments_complete: false,
  });
  const presented = presentItem(tool);
  expect(parseItemValue(presented.arguments, presented.incomplete)).toBe("{}");
  expect(state.incomplete).toBe(true);
});
