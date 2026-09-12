import { expect, it } from "vitest";
import type { RunEvent } from "@converge.ai/a13n";
import { applyExecution, type Execution } from "./execution";

function event(
  type: string,
  payload: RunEvent["event"]["payload"],
  item?: string,
  scope = "root",
): RunEvent {
  return {
    cursor: `${++sequence}-0`,
    event: {
      event_type: type,
      event_id: `event-${sequence}`,
      run_id: "run",
      thread_id: "thread",
      occurred_at: "2026-09-12T00:00:00Z",
      payload,
      item_id: item,
      run_attempt_id: "attempt",
      harness_run_id: scope,
    },
  };
}
let sequence = 0;
function lifecycle(type: string, request = "request-1", scope = "root") {
  return event(
    "agui.custom",
    {
      name: "a13n.harness.lifecycle",
      value: { event: { payload: { type, request_id: request } } },
    },
    undefined,
    scope,
  );
}
it("counts model boundaries, excludes input, and keeps tools with their request", () => {
  let state: Execution = { steps: [], items: new Map() };
  for (const entry of [
    event(
      "agui.text_message_start",
      { role: "user", item_kind: "text_message" },
      "input",
    ),
    lifecycle("model_request_started"),
    event(
      "agui.text_message_start",
      { role: "user", item_kind: "text_message", metadata: { display: false } },
      "context",
    ),
    event(
      "agui.tool_call_start",
      { item_kind: "tool_call", toolCallName: "search" },
      "tool",
    ),
    lifecycle("model_request_completed"),
    lifecycle("model_request_started", "request-2"),
    event("agui.tool_call_result", { content: "Found" }, "tool"),
    event(
      "agui.text_message_start",
      { role: "assistant", item_kind: "text_message" },
      "reply",
    ),
    lifecycle("model_request_completed", "request-2"),
  ])
    state = applyExecution(state, entry);
  expect(state.steps.map(({ state, items }) => ({ state, items }))).toEqual([
    { state: "completed", items: ["tool"] },
    { state: "completed", items: ["reply"] },
  ]);
});
it("deduplicates starts and separates child requests with the same request ID", () => {
  let state: Execution = { steps: [], items: new Map() };
  for (const entry of [
    lifecycle("model_request_started"),
    lifecycle("model_request_started"),
    lifecycle("model_request_started", "request-1", "child"),
    lifecycle("model_request_failed", "request-1", "child"),
  ])
    state = applyExecution(state, entry);
  expect(state.steps).toHaveLength(2);
  expect(state.steps.map((step) => step.state)).toEqual(["running", "failed"]);
});

it("counts a replacement attempt independently and leaves prior snapshots unchanged", () => {
  const initial: Execution = { steps: [], items: new Map() };
  const first = applyExecution(initial, lifecycle("model_request_started"));
  const next = lifecycle("model_request_started");
  next.event.run_attempt_id = "replacement-attempt";
  const replacement = applyExecution(first, next);
  const completed = applyExecution(
    replacement,
    lifecycle("model_request_completed"),
  );
  expect(first.steps).toHaveLength(1);
  expect(first.steps[0]?.state).toBe("running");
  expect(completed.steps.map((step) => step.state)).toEqual([
    "completed",
    "running",
  ]);
});
