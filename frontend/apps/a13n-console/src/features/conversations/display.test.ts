import { beforeEach, expect, it, vi } from "vitest";
import { DisplayNormalizer, isItemRef } from "a13n-ui/display";
import { createClient, type SessionDelta } from "../../service-client";
import { fixtureRun } from "./transcript/fixture";
import {
  applyDelta,
  comparePositions,
  isOmitted,
  readDisplay,
  readRunDisplay,
  readEarlierDisplay,
  type DisplayItem,
} from "./display";
import { SessionDisplays } from "./session-displays";

let frames: (() => void)[] = [];
const nextFrame = () => frames.splice(0).forEach((frame) => frame());
beforeEach(() => {
  frames = [];
  vi.stubGlobal("requestAnimationFrame", (frame: () => void) =>
    frames.push(frame),
  );
});

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
const delta = (sequence: number, text = "!"): SessionDelta => ({
  thread_id: "thread_2",
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
const snapshot = (
  items: DisplayItem[],
  { first = 1, complete = false } = {},
) => ({
  thread_id: "thread_2",
  run_id: "run_2",
  first,
  items,
  continuation: null,
  complete,
});

it("reads the committed display of one Run", async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    expect(new URL((input as Request).url).pathname).toBe(
      "/api/v1/runs/run_2/items",
    );
    return Response.json({
      run: fixtureRun(),
      items: [item(4)],
      complete: true,
    });
  });
  const client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch,
  });
  expect(await readDisplay(client, "ws_1", "run_2")).toMatchObject({
    items: [item(4)],
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

it("does not append live deltas to a saved preview or lose the full-content reference", () => {
  const original = item(1, "Preview");
  original.content_refs = {
    text: {
      id: "cnt_one",
      size_bytes: 50000,
      media_type: "text/plain",
      preview: "Preview",
    },
  };
  const items = new Map([[original.id, original]]);
  applyDelta(items, delta(2, "suffix"));
  expect(items.get("message")?.content.text).toBe("Preview");
  expect(items.get("message")?.content_refs).toEqual(original.content_refs);
  expect(items.get("message")?.last_stream_id).toBe("1-2");
});

const text = (displays: SessionDisplays) =>
  displays.get("run_2")?.items.map((item) => item.content.text);

it("replaces held items from a snapshot's first ordinal on and folds later deltas", () => {
  const displays = new SessionDisplays();
  const done = { ...item(1, "Done"), id: "done", state: "completed" as const };
  displays.items(snapshot([done, { ...item(1), ordinal: 2 }]));
  displays.delta(delta(2));
  expect(text(displays)).toEqual(["Done", "Hello!"]);
  // A repair from ordinal 2 keeps the final item before it.
  displays.items(
    snapshot([{ ...item(3, "Hello!!"), ordinal: 2 }], { first: 2 }),
  );
  expect(displays.get("run_2")).toMatchObject({ first: 1, partial: false });
  expect(text(displays)).toEqual(["Done", "Hello!!"]);
});

it("holds a run's output after a gap until its next items", () => {
  const displays = new SessionDisplays();
  displays.items(snapshot([item(1)]));
  displays.gap("run_2");
  displays.delta(delta(5, " lost"));
  expect(displays.get("run_2")).toMatchObject({ partial: true });
  expect(displays.get("run_2")?.items[0]?.content).toMatchObject({
    text: "Hello",
    incomplete: true,
  });
  displays.items(snapshot([item(5, "Hello again")]));
  displays.delta(delta(6));
  expect(displays.get("run_2")).toMatchObject({ partial: false });
  expect(text(displays)).toEqual(["Hello again!"]);
  // The final display ends live output.
  displays.items(snapshot([item(6, "Hello again!")], { complete: true }));
  displays.delta(delta(7));
  expect(text(displays)).toEqual(["Hello again!"]);
});

it("keeps no earlier items after a delta fails to apply", () => {
  const displays = new SessionDisplays();
  const done = { ...item(1, "Done"), id: "done", state: "completed" as const };
  displays.items(snapshot([done, { ...item(1), ordinal: 2 }]));
  displays.delta({ ...delta(2), event: null as never });
  expect(displays.get("run_2")).toMatchObject({ partial: true });
  // The stream does not know this display missed output, so earlier items are read again.
  displays.items(
    snapshot([{ ...item(9, "Hello all"), ordinal: 3 }], { first: 3 }),
  );
  expect(displays.get("run_2")).toMatchObject({ first: 3, partial: false });
  expect(text(displays)).toEqual(["Hello all"]);
});

it("replaces unfinished displays from each new connection's snapshot", () => {
  const displays = new SessionDisplays();
  const listener = vi.fn();
  displays.subscribe("run_2", listener);
  displays.items(
    snapshot([
      { ...item(1, "Earlier"), id: "earlier" },
      { ...item(1), ordinal: 2 },
    ]),
  );
  displays.connected();
  // Items an earlier connection held may have missed output, even before the new first ordinal.
  displays.items(
    snapshot([{ ...item(9, "Hello all"), ordinal: 2 }], { first: 2 }),
  );
  expect(displays.get("run_2")).toMatchObject({ first: 2 });
  expect(text(displays)).toEqual(["Hello all"]);
  // A held run that ended while disconnected is dropped, so its committed display is read.
  expect(displays.connected()).toEqual(["run_2"]);
  expect(displays.get("run_2")).toBeUndefined();
  expect(listener).not.toHaveBeenCalled();
  nextFrame();
  expect(listener).toHaveBeenCalledOnce();
  // On a live connection the thread frame precedes the run's final items, which keep it.
  displays.items(snapshot([item(1)]));
  expect(displays.get("run_2")).toBeDefined();
});

it("bounds completed history while keeping active and observed output", () => {
  const displays = new SessionDisplays();
  displays.items(snapshot([item(1)]));
  const unsubscribe = displays.subscribe("old", () => undefined);
  displays.items({ ...snapshot([item(1)], { complete: true }), run_id: "old" });
  for (let index = 0; index < 25; index++)
    displays.items({
      ...snapshot([item(1)], { complete: true }),
      run_id: `completed_${index}`,
    });
  expect(displays.get("run_2")).toBeDefined();
  expect(displays.get("old")).toBeDefined();
  expect(displays.get("completed_0")).toBeUndefined();
  expect(displays.get("completed_24")).toBeDefined();
  unsubscribe();
  expect(displays.get("old")).toBeUndefined();
  // No Thread frame is necessary to invalidate output that ended outside the new window.
  expect(displays.connected()).toEqual(["run_2"]);
  expect(displays.get("run_2")).toBeUndefined();
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
      delta: SessionDelta;
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
  const displays = new SessionDisplays();
  displays.items(snapshot([]));
  const ref = {
    id: "tool",
    kind: "tool_call" as const,
    state: "in_progress" as const,
  };
  displays.delta({
    ...delta(3),
    item: ref,
    event: { type: "TOOL_CALL_ARGS", toolCallId: "t", delta: "{}" },
  });
  displays.delta({
    ...delta(4),
    item: ref,
    event: { type: "TOOL_CALL_END", toolCallId: "t" },
  });
  const tool = displays.get("run_2")!.items[0]!;
  expect(tool.content).toMatchObject({
    arguments: "{}",
    incomplete: true,
    arguments_complete: false,
  });
  const presented = presentItem(tool);
  expect(parseItemValue(presented.arguments, presented.incomplete)).toBe("{}");
  expect(displays.get("run_2")?.partial).toBe(true);
});

it("keeps reply memory sources from END metadata without altering the answer", () => {
  const normalizer = new DisplayNormalizer();
  const items = new Map<string, DisplayItem>();
  normalizer.apply(items, delta(1, "Hello"));
  const sources = [
    {
      kind: "record",
      memory: "facts",
      memory_id: "mem_1",
      status: "recalled",
      records: [{ id: "r1", sha256: "digest" }],
    },
  ];
  const end = {
    ...delta(2),
    event: { type: "TEXT_MESSAGE_END", messageId: "m" },
    item: {
      ...delta(2).item!,
      state: "completed" as const,
      memory_sources: sources,
    },
  };
  expect(isItemRef(end.item)).toBe(true);
  normalizer.apply(items, end);
  expect(items.get(end.item.id)?.content.text).toBe("Hello");
  expect(items.get(end.item.id)?.content.memory_sources).toEqual(sources);
  sources[0].records[0].id = "changed";
  expect(
    (items.get(end.item.id)?.content.memory_sources as typeof sources)[0]
      .records[0].id,
  ).toBe("r1");
  expect(isItemRef({ ...end.item, memory_sources: [null] })).toBe(false);
});

const committed = (complete = false) => ({
  run: fixtureRun(),
  items: [item()],
  complete,
});

it("shows only the latest window of a large Run before any history request", async () => {
  const requests: URL[] = [];
  const items = Array.from({ length: 867 }, (_, index) => ({
    ...item(),
    id: `item_${index + 1}`,
    ordinal: index + 1,
  }));
  const client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch: async (input) => {
      const url = new URL((input as Request).url);
      requests.push(url);
      const after = url.searchParams.get("after");
      return Response.json(
        after === null
          ? { ...committed(), items: items.slice(-200) }
          : {
              ...committed(true),
              items: items.slice(
                Number(after),
                Number(after) + Number(url.searchParams.get("limit")),
              ),
            },
      );
    },
  });
  const result = await readRunDisplay(client, "ws_1", "run_2");
  expect(result.items).toEqual(items.slice(-200));
  expect(result).toMatchObject({ complete: false, immutableBefore: 668 });
  expect(requests.map((url) => url.search)).toEqual([""]);
  client.close();
});

it("reuses immutable pages but refreshes a former mutable tail before retaining it", async () => {
  const requests: URL[] = [];
  const items = Array.from({ length: 5 }, (_, index) => ({
    ...item(),
    id: `item_${index + 1}`,
    ordinal: index + 1,
  }));
  let first = 3;
  const client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch: async (input) => {
      const url = new URL((input as Request).url);
      requests.push(url);
      const after = url.searchParams.get("after");
      const before = url.searchParams.get("before");
      return Response.json({
        ...committed(),
        items:
          before !== null
            ? items.slice(0, Number(before) - 1)
            : after === null
              ? [items[first - 1]]
              : items.slice(
                  Number(after),
                  Number(after) + Number(url.searchParams.get("limit")),
                ),
      });
    },
  });
  const initial = await readRunDisplay(client, "ws_1", "run_2");
  const previous = await readEarlierDisplay(
    client,
    "ws_1",
    "run_2",
    initial,
    3,
  );
  first = 5;
  items[2] = {
    ...items[2]!,
    content: { ...items[2]!.content, text: "Final answer" },
    state: "completed",
  };
  const next = await readRunDisplay(
    client,
    "ws_1",
    "run_2",
    undefined,
    previous,
  );
  expect(next.items).toEqual(items);
  expect(previous.items[2]!.content.text).toBe("Hello");
  expect(requests.map((url) => url.search)).toEqual([
    "",
    "?before=3&limit=200",
    "",
    "?after=2&limit=2",
  ]);
  client.close();
});

it.each([[], [2], [1, 1]].map((ordinals) => ({ ordinals })))(
  "rejects missing or non-advancing history $ordinals without looping",
  async ({ ordinals }) => {
    let requests = 0;
    const client = createClient({
      baseUrl: "https://test.invalid",
      auth: { type: "session" },
      fetch: async (input) => {
        requests++;
        const historical = new URL((input as Request).url).searchParams.has(
          "before",
        );
        return Response.json({
          ...committed(),
          items: (historical ? ordinals : [3]).map((ordinal) => ({
            ...item(),
            ordinal,
          })),
        });
      },
    });
    const current = await readRunDisplay(client, "ws_1", "run_2");
    await expect(
      readEarlierDisplay(client, "ws_1", "run_2", current, 3),
    ).rejects.toThrow("Earlier run content could not be loaded.");
    expect(requests).toBe(2);
    client.close();
  },
);
