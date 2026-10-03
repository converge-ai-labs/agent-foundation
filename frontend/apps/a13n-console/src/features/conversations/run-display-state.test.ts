import { expect, it } from "vitest";
import type { ThreadDelta } from "../../service-client";
import type { Schema } from "../../shared/api";
import { fixtureRun } from "./transcript/fixture";
import { RunDisplayState } from "./run-display-state";

const item = (sequence: number, text = "x"): Schema["Item"] => ({
  id: "message",
  ordinal: 1,
  kind: "text_message",
  state: "in_progress",
  first_stream_id: "1-1",
  last_stream_id: `1-${sequence}`,
  started_at: "2026-09-20T10:00:00Z",
  ended_at: null,
  content: { text },
});
const baseline = (sequence: number, text = "x"): Schema["RunItems"] => ({
  run: fixtureRun({ status: "running" }),
  items: [item(sequence, text)],
  position: `1-${sequence}`,
  complete: false,
});
const delta = (sequence: number): ThreadDelta => ({
  run_id: "run",
  attempt: 1,
  sequence,
  changes: [
    {
      type: "append",
      id: "message",
      field: "text",
      text: "x",
      last_stream_id: `1-${sequence}`,
      state: "in_progress",
      ended_at: null,
    },
  ],
});

it("keeps thousands of applied tokens as one compact item across a behind read", () => {
  const state = new RunDisplayState();
  state.reconcile(baseline(1), 1);
  for (let sequence = 2; sequence <= 5000; sequence++)
    state.receive(delta(sequence), `c${sequence}`);
  state.reconcile(baseline(2, "xx"), 1);
  expect(state.items.size).toBe(1);
  expect(state.items.get("message")?.content.text).toBe("x".repeat(5000));
  expect(state.resume("run")).toEqual({
    run: "run",
    position: "1-5000",
    after: "c5000",
  });
  expect(state.incomplete).toBe(false);
});

it("refreshes a fully delivered quiet boundary once without regressing its cursor", () => {
  const state = new RunDisplayState();
  state.reconcile(baseline(1), 1);
  state.receive(delta(2), "c2");
  state.receive(delta(3), "c3");
  expect(state.boundary(1, 2, "late-boundary")).toBe(true);
  expect(state.resume("run")?.after).toBe("c3");
  expect(state.boundary(1, 2, "late-boundary")).toBe(false);
  state.reconcile(baseline(2, "xx"), 1);
  expect(state.items.get("message")?.content.text).toBe("xxx");
  expect(state.boundary(1, 3, "b3")).toBe(true);
  state.reconcile(
    {
      ...baseline(3, "xxx"),
      items: [{ ...item(3, "xxx"), state: "interrupted" }],
    },
    1,
  );
  expect(state.items.get("message")?.state).toBe("interrupted");
});

it("rejects stale HTTP baselines without losing the new tail", () => {
  const state = new RunDisplayState();
  state.reconcile(baseline(5, "new"), 1);
  state.reconcile(baseline(2, "old"), 1);
  expect(state.items.get("message")?.content.text).toBe("new");
  expect(state.read?.position).toBe("1-5");
});

it("retains bounded pending output beyond a hole and recovers from a covering baseline", () => {
  const state = new RunDisplayState();
  state.reconcile(baseline(1), 1);
  for (let sequence = 3; sequence <= 3000; sequence++)
    state.receive(delta(sequence), `c${sequence}`);
  expect(state.position).toBe("1-1");
  expect(state.incomplete).toBe(true);
  state.reconcile(baseline(2999, "recovered"), 1);
  expect(state.items.get("message")?.content.text).toBe("recoveredx");
  expect(state.position).toBe("1-3000");
  expect(state.incomplete).toBe(false);
});
