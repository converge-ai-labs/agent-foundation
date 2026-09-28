import { expect, it } from "vitest";
import type { Schema } from "../../shared/api";
import { attemptEvent, retryEvent, runOutcome } from "./lifecycle";
import { fixtureAttempt } from "./transcript/fixture";

const run = (overrides: Partial<Schema["RunView"]>) =>
  ({
    id: "run_1",
    status: "completed",
    failure: null,
    wait_reason: null,
    sealed_at: "2026-09-20T10:00:12.000Z",
    updated_at: "2026-09-20T10:00:12.000Z",
    ...overrides,
  }) as Schema["RunView"];

it("says nothing about the first attempt every run starts with", () => {
  expect(attemptEvent(fixtureAttempt(1))).toBe(null);
});

it("reports a later attempt with why it started", () => {
  expect(
    attemptEvent(
      fixtureAttempt(2, {
        created_at: "2026-09-20T10:00:02.000Z",
        started_at: "2026-09-20T10:00:03.000Z",
      }),
    ),
  ).toMatchObject({
    kind: "event",
    id: "att_2",
    notice: {
      kind: "attempt",
      tone: "neutral",
      attempt: 2,
      reason: "recovery",
    },
    occurredAt: "2026-09-20T10:00:03.000Z",
  });
  // An attempt not yet started is placed when it was created.
  expect(
    attemptEvent(
      fixtureAttempt(2, {
        created_at: "2026-09-20T10:00:02.000Z",
        started_at: null,
      }),
    )?.occurredAt,
  ).toBe("2026-09-20T10:00:02.000Z");
});

it("reports a scheduled model retry", () => {
  expect(
    retryEvent({
      id: "obs_retry",
      position: "1-4",
      occurredAt: "2026-09-20T10:00:04.000Z",
      attempt: 2,
      maxAttempts: 3,
      delaySeconds: 1.5,
    }),
  ).toMatchObject({
    id: "obs_retry",
    notice: {
      kind: "retry",
      tone: "warning",
      attempt: 2,
      maxAttempts: 3,
      delaySeconds: 1.5,
    },
    occurredAt: "2026-09-20T10:00:04.000Z",
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
    id: "run_1:outcome",
    notice: {
      kind: "outcome",
      status: "failed",
      tone: "danger",
      reason: "model_rate_limited",
      message: "Upstream said no.",
    },
  });
  expect(runOutcome(run({ status: "cancelled" }))?.notice).toMatchObject({
    status: "cancelled",
    tone: "warning",
  });
  expect(
    runOutcome(
      run({
        status: "waiting",
        wait_reason: "approval",
        sealed_at: "2026-09-20T10:00:05.000Z",
      }),
    ),
  ).toMatchObject({
    notice: { status: "waiting", reason: "approval", message: null },
    occurredAt: "2026-09-20T10:00:05.000Z",
  });
});

it("keeps the pending category summary", () => {
  for (const reason of ["approval", "call", "multiple", null] as const)
    expect(
      runOutcome(run({ status: "waiting", wait_reason: reason }))?.notice,
    ).toMatchObject({ status: "waiting", reason });
});
