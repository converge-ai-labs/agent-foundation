import { expect, it } from "vitest";
import type { Schema } from "../../shared/api";
import { lifecycleNotice, runOutcome } from "./lifecycle";
import type { EventEntry } from "./timeline";

function fact(type: string, fields: Partial<EventEntry> = {}): EventEntry {
  return {
    kind: "event",
    id: "event",
    type,
    code: null,
    message: null,
    attempt: null,
    maxAttempts: null,
    delaySeconds: null,
    occurredAt: "2026-09-20T10:00:00.000Z",
    startedAt: "2026-09-20T10:00:00.000Z",
    endedAt: "2026-09-20T10:00:00.000Z",
    durationMs: null,
    state: "observed",
    ...fields,
  };
}

const run = (overrides: Partial<Schema["RunResource"]>) =>
  ({
    id: "run_1",
    status: "completed",
    failure: null,
    wait_reason: null,
    completed_at: "2026-09-20T10:00:12.000Z",
    waiting_at: null,
    updated_at: "2026-09-20T10:00:12.000Z",
    ...overrides,
  }) as Schema["RunResource"];

it("says nothing about the facts every run reports", () => {
  expect(lifecycleNotice(fact("run.accepted"))).toBe(null);
  expect(lifecycleNotice(fact("run_attempt.leased", { attempt: 1 }))).toBe(
    null,
  );
  expect(lifecycleNotice(fact("run_attempt.started"))).toBe(null);
  expect(lifecycleNotice(fact("run.running"))).toBe(null);
  expect(lifecycleNotice(fact("run.completed"))).toBe(null);
});

it("reports a later attempt, a recovery gap and a scheduled retry", () => {
  expect(
    lifecycleNotice(
      fact("run_attempt.leased", { attempt: 2, code: "recovery" }),
    ),
  ).toEqual({
    kind: "attempt",
    tone: "neutral",
    attempt: 2,
    reason: "recovery",
  });
  expect(
    lifecycleNotice(fact("run.recovery", { code: "worker_replaced" })),
  ).toEqual({ kind: "recovery", tone: "warning", reason: "worker_replaced" });
  expect(
    lifecycleNotice(
      fact("model_retry_scheduled", {
        attempt: 2,
        maxAttempts: 3,
        delaySeconds: 1.5,
      }),
    ),
  ).toEqual({
    kind: "retry",
    tone: "warning",
    attempt: 2,
    maxAttempts: 3,
    delaySeconds: 1.5,
  });
});

it("keeps the terminal facts a reader has to act on", () => {
  expect(lifecycleNotice(fact("run.failed", { code: "boom" }))).toMatchObject({
    kind: "outcome",
    status: "failed",
    tone: "danger",
  });
  expect(lifecycleNotice(fact("run.cancelled"))).toMatchObject({
    status: "cancelled",
    tone: "warning",
  });
  expect(lifecycleNotice(fact("run.waiting"))).toMatchObject({
    status: "waiting",
  });
});

it("says who a waiting run is waiting on", () => {
  for (const reason of ["approval", "user_input", "multiple", null])
    expect(
      lifecycleNotice(fact("run.waiting", { code: reason })),
    ).toMatchObject({ status: "waiting", waitingOn: "reader" });
  // Only the application that holds the client tool can return its result.
  expect(
    lifecycleNotice(fact("run.waiting", { code: "client_tool" })),
  ).toMatchObject({ status: "waiting", waitingOn: "application" });
  expect(lifecycleNotice(fact("run.failed"))).toMatchObject({
    waitingOn: null,
  });
});

it("reads the run's own outcome, and only when it has one to report", () => {
  expect(runOutcome(run({ status: "completed" }))).toBe(null);
  expect(runOutcome(run({ status: "running" }))).toBe(null);
  expect(
    runOutcome(
      run({
        status: "failed",
        failure: { code: "model_rate_limited", message: "Upstream said no." },
      }),
    ),
  ).toMatchObject({
    type: "run.failed",
    code: "model_rate_limited",
    message: "Upstream said no.",
  });
  expect(
    runOutcome(
      run({
        status: "waiting",
        wait_reason: "approval",
        completed_at: null,
        waiting_at: "2026-09-20T10:00:05.000Z",
      }),
    ),
  ).toMatchObject({
    type: "run.waiting",
    code: "approval",
    occurredAt: "2026-09-20T10:00:05.000Z",
  });
});
