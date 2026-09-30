import { expect, it, vi } from "vitest";
import type { DisplayBlock, DisplaySnapshot } from "a13n-ui/display";
import { createClient } from "../../service-client";
import { fixtureRun } from "./transcript/fixture";
import {
  comparePositions,
  displayItems,
  isOmitted,
  readDisplay,
} from "./display";
import { RunDisplayState } from "./run-display-state";
import { runExecution } from "./execution";

const block = (fields: Partial<DisplayBlock> = {}): DisplayBlock => ({
  id: "block",
  scope_id: "scope",
  kind: "text",
  status: "running",
  revision: 1,
  message_index: 0,
  part_index: 0,
  content: { text: "Hello" },
  ...fields,
});
const snapshot = (sequence = 1): DisplaySnapshot => ({
  format: "display/1",
  position: { producer: { run_id: "run", generation: "1" }, sequence },
  scopes: [
    {
      id: "scope",
      run_id: "harness",
      thread_id: "thread",
      parent_scope_id: null,
      parent_tool_call_id: null,
      invocation_id: null,
      status: "running",
    },
  ],
  blocks: [block()],
  omitted: 0,
  continuity: {},
});

it("reads a committed compact baseline", async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    expect(new URL((input as Request).url).pathname).toBe(
      "/api/v1/runs/run/items",
    );
    return Response.json({
      run: fixtureRun(),
      snapshot: snapshot(),
      position: "1-1",
      complete: true,
      display_revision: "r1",
    });
  });
  const client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch,
  });
  expect(
    await readDisplay(client, "ws", "run", new AbortController().signal),
  ).toMatchObject({ position: "1-1", complete: true });
  client.close();
});

it("orders decimal display positions numerically without rounding", () => {
  expect(comparePositions("100-10", "100-2")).toBeGreaterThan(0);
  expect(comparePositions("2-1", "10-0")).toBeLessThan(0);
  expect(comparePositions("3-7", "3-7")).toBe(0);
  expect(
    comparePositions("9007199254740993-0", "9007199254740992-0"),
  ).toBeGreaterThan(0);
  expect(() => comparePositions("1-01", "1-2")).toThrow();
});

it("adapts blocks for presentation without folding events or inventing times", () => {
  const value = snapshot();
  value.blocks = [
    block({
      kind: "reasoning",
      content: { text: "Think", signature: "sealed" },
    }),
    block({
      id: "tool",
      kind: "tool_chunk",
      status: "failed",
      content: {
        name: "read",
        tool_call_id: "call",
        arguments: "{}",
        result: "Denied",
      },
    }),
  ];
  const items = [...displayItems(value).values()];
  expect(items[0]).toMatchObject({
    kind: "reasoning_message",
    started_at: null,
    content: { encrypted_value: "sealed" },
  });
  expect(items[1]).toMatchObject({
    kind: "tool_call",
    state: "failed",
    content: { toolCallName: "read", result: "Denied" },
  });
  expect([...displayItems(snapshot(), true).values()][0]?.state).toBe(
    "interrupted",
  );
});

it("recognizes retained content truncation", () => {
  expect(isOmitted({ truncated: true, text: "kept prefix" })).toBe(true);
  expect(isOmitted({})).toBe(false);
});

it("rejects stale durable responses rather than rolling back its baseline", () => {
  const state = new RunDisplayState();
  const newer = {
    run: fixtureRun({ id: "run", version: 3 }),
    snapshot: snapshot(3),
    position: "1-3",
    complete: false,
    display_revision: "r3",
  };
  state.reconcile(newer, 1);
  state.reconcile(
    {
      ...newer,
      snapshot: snapshot(1),
      position: "1-1",
      display_revision: "r1",
    },
    1,
  );
  expect(state.position).toBe("1-3");
  state.reconcile(
    {
      ...newer,
      run: fixtureRun({ id: "run", version: 2 }),
      display_revision: "old",
    },
    1,
  );
  expect(state.read?.display_revision).toBe("r3");
});

it.each(["count", "bytes"])(
  "bounds its %s suffix without rolling back to an unreplayable baseline",
  (limit) => {
    const state = new RunDisplayState();
    const read = {
      run: fixtureRun({ id: "run", version: 3 }),
      snapshot: snapshot(),
      position: "1-1",
      complete: false,
      display_revision: "r1",
    };
    state.reconcile(read, 1);
    const count = limit === "count" ? 1030 : 3;
    const text = limit === "count" ? "x" : "x".repeat(2 * 1024 * 1024);
    for (let sequence = 2; sequence <= count; sequence++)
      state.receive(
        {
          run_id: "run",
          attempt: 1,
          sequence,
          delta: {
            format: "display-delta/1",
            producer: { run_id: "run", generation: "1" },
            from_sequence: sequence - 1,
            through_sequence: sequence,
            operations: [
              {
                op: "block.append",
                id: "block",
                field: "text",
                value: text,
                expected_revision: sequence - 1,
                revision: sequence,
              },
            ],
          },
        },
        `${sequence}-0`,
      );
    state.reconcile(read, 1);
    expect(state.position).toBe(`1-${count}`);
    expect(state.incomplete).toBe(false);
    const final = snapshot(count);
    final.blocks = [block({ revision: count, content: { text: "saved" } })];
    state.reconcile(
      {
        ...read,
        snapshot: final,
        position: `1-${count}`,
        display_revision: "new",
      },
      1,
    );
    expect([...state.items.values()][0]?.content.text).toBe("saved");
  },
);

it("renders compact execution summaries and inline provenance without raw lifecycle events", () => {
  const value = snapshot();
  value.scopes.push({
    ...value.scopes[0]!,
    id: "child",
    run_id: "child",
    parent_scope_id: "scope",
    parent_tool_call_id: "call",
    invocation_id: "invocation",
    status: "completed",
  });
  value.blocks = [
    block({
      id: "request",
      kind: "extension",
      status: "succeeded",
      content: {
        name: "a13n.display.model_request",
        value: { message_count: 3, request_tokens: 42 },
      },
    }),
    block({
      id: "scope:tool:call",
      kind: "tool_chunk",
      status: "succeeded",
      content: { name: "delegate", tool_call_id: "call", arguments: "{}" },
    }),
    block({
      id: "child-request",
      scope_id: "child",
      kind: "extension",
      status: "succeeded",
      content: {
        name: "a13n.display.model_request",
        value: { message_count: 1 },
      },
    }),
    block({
      id: "child:tool:call",
      scope_id: "child",
      kind: "tool_chunk",
      status: "succeeded",
      content: {
        name: "web_search",
        native: true,
        tool_call_id: "call",
        result: "found",
      },
    }),
    block({
      id: "child-answer",
      scope_id: "child",
      status: "succeeded",
      content: { text: "Answer" },
    }),
  ];
  const execution = runExecution(fixtureRun(), [
    ...displayItems(value, true).values(),
  ]);
  expect(execution.steps.find((step) => step.id === "request")).toMatchObject({
    kind: "llm",
    state: "completed",
    contextTokens: 42,
    messageCount: 3,
    items: ["scope:tool:call"],
  });
  expect(
    execution.steps.find((step) => step.id === "scope:tool:call"),
  ).toMatchObject({
    kind: "subagent",
    dispatchOnly: false,
    state: "completed",
  });
  expect(
    execution.steps.find((step) => step.id === "child-request"),
  ).toMatchObject({
    parentId: "scope:tool:call",
    items: ["child:tool:call", "child-answer"],
  });
  expect(
    execution.steps.find((step) => step.id === "child:tool:call"),
  ).toMatchObject({
    kind: "tool",
    scope: "child",
    native: true,
    state: "completed",
    parentId: "scope:tool:call",
  });
});
