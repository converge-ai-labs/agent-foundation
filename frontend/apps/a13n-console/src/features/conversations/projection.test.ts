import type { RunEvent } from "../../service-client";
import { describe, expect, it } from "vitest";
import {
  applyRunEvent,
  compareCursors,
  mergeRetainedItems,
  type PresentedItem,
} from "./projection";
function event(
  cursor: string,
  event_type: string,
  payload: Record<string, unknown>,
): RunEvent {
  return {
    cursor,
    event: {
      event_id: `rse_${cursor}`,
      event_type,
      run_id: "run_test",
      thread_id: "thread_test",
      item_id: "itm_message",
      occurred_at: "2026-09-08T00:00:00Z",
      payload,
    },
  };
}
describe("Run presentation checkpoints", () => {
  it("compares stream sequence numbers numerically without losing precision", () => {
    expect(compareCursors("100-10", "100-2")).toBeGreaterThan(0);
    expect(
      compareCursors("9007199254740993-0", "9007199254740992-0"),
    ).toBeGreaterThan(0);
  });
  it("deduplicates replay against retained item boundaries before appending new deltas", () => {
    let items = mergeRetainedItems(new Map(), [
      {
        id: "itm_message",
        kind: "text_message",
        state: "interrupted",
        parent_item_id: null,
        first_stream_id: "100-0",
        last_stream_id: "100-1",
        content: { text: "Hello" },
      },
    ]);
    items = applyRunEvent(
      items,
      event("100-1", "agui.text_message_content", { delta: "Hello" }),
    );
    items = applyRunEvent(
      items,
      event("100-2", "agui.text_message_content", { delta: " world" }),
    );
    items = applyRunEvent(
      items,
      event("100-3", "item.completed", { item_state: "completed" }),
    );
    expect(items.get("itm_message")?.text).toBe("Hello world");
    expect(items.get("itm_message")?.lastCursor).toBe("100-3");
    expect(items.get("itm_message")?.state).toBe("completed");
  });
  it("does not let an older snapshot overwrite newer streamed content", () => {
    let items = new Map<string, PresentedItem>();
    items = applyRunEvent(
      items,
      event("200-1", "agui.text_message_content", {
        item_kind: "text_message",
        delta: "New",
      }),
    );
    items = mergeRetainedItems(items, [
      {
        id: "itm_message",
        kind: "text_message",
        state: "interrupted",
        parent_item_id: null,
        first_stream_id: "100-0",
        last_stream_id: "100-1",
        content: {},
      },
    ]);
    expect(items.get("itm_message")?.text).toBe("New");
  });
  it("retains tool arguments and distinguishes interrupted presentation from tool success", () => {
    let items = applyRunEvent(
      new Map(),
      event("100-0", "agui.tool_call_start", {
        item_kind: "tool_call",
        toolCallName: "read_file",
      }),
    );
    items = applyRunEvent(
      items,
      event("100-1", "agui.tool_call_args", { delta: '{"path":' }),
    );
    items = applyRunEvent(
      items,
      event("100-2", "item.interrupted", {
        item_state: "interrupted",
        interruption: { code: "run_closed" },
      }),
    );
    expect(items.get("itm_message")).toMatchObject({
      toolName: "read_file",
      arguments: '{"path":',
      state: "interrupted",
    });
    expect(items.get("itm_message")?.result).toBeUndefined();
  });
});

it("does not interrupt an Item already read beyond a replayed recovery event", () => {
  const items = mergeRetainedItems(new Map(), [
    {
      id: "new",
      kind: "text_message",
      state: "in_progress",
      parent_item_id: null,
      first_stream_id: "6-0",
      last_stream_id: "8-0",
      content: { text: "after recovery" },
    },
  ]);
  const recovered = applyRunEvent(items, {
    cursor: "5-0",
    event: {
      schema_version: "1",
      event_id: "event_recovery",
      event_type: "run.recovery",
      run_id: "run",
      thread_id: "thread",
      item_id: null,
      occurred_at: "2026-09-17T00:00:00Z",
      payload: {},
    },
  });
  expect(recovered.get("new")?.state).toBe("in_progress");
});

it("times an Item from its first and terminal observation and never from a snapshot", () => {
  let items = applyRunEvent(
    new Map(),
    event("300-0", "agui.text_message_start", {
      item_kind: "text_message",
      role: "assistant",
    }),
  );
  expect(items.get("itm_message")).toMatchObject({
    startedAt: "2026-09-08T00:00:00Z",
    endedAt: null,
  });
  items = applyRunEvent(
    items,
    event("300-1", "item.completed", { item_state: "completed" }),
  );
  expect(items.get("itm_message")?.endedAt).toBe("2026-09-08T00:00:00Z");
  const retained = mergeRetainedItems(new Map(), [
    {
      id: "itm_snapshot",
      kind: "text_message",
      state: "completed",
      parent_item_id: null,
      first_stream_id: "100-0",
      last_stream_id: "100-1",
      content: { text: "Hello" },
    },
  ]);
  expect(retained.get("itm_snapshot")).toMatchObject({
    startedAt: null,
    endedAt: null,
  });
});

it("keeps steering provenance so an enqueued notice is not read as authored input", () => {
  const items = applyRunEvent(
    new Map(),
    event("400-0", "agui.text_message_start", {
      item_kind: "text_message",
      role: "user",
      metadata: { "a13n.steering-source": "async_subagent" },
    }),
  );
  expect(items.get("itm_message")?.steeringSource).toBe("async_subagent");
});
