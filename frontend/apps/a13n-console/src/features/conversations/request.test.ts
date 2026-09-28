import { expect, it } from "vitest";
import type { Schema } from "../../shared/api";
import { runRequest } from "./request";

const run = (overrides: Partial<Schema["RunView"]>) =>
  ({
    id: "run_1",
    trigger: "input",
    input: null,
    resume: null,
    ...overrides,
  }) as Schema["RunView"];

const thread = (origin: Schema["ThreadView"]["origin"]) =>
  ({ id: "thr_1", origin }) as Schema["ThreadView"];

const message = (text: string) => ({ content: [{ type: "text", text }] });

it("reads ordinary input as the message a person sent", () => {
  const request = runRequest(
    run({ input: message("Review the release") }),
    thread("new"),
  );
  expect(request).toMatchObject({
    kind: "message",
    text: "Review the release",
  });
});

it("keeps the answers that resumed a wait as feedback", () => {
  const resume = {
    approvals: { call_1: { action: "approve" as const } },
    calls: {},
  };
  expect(
    runRequest(run({ trigger: "resume", resume }), thread("new")),
  ).toMatchObject({ kind: "feedback", input: resume });
});

it("reads an asynchronous child's result as prose, not as its envelope", () => {
  const request = runRequest(
    run({
      trigger: "child_result",
      input: {
        child_run_id: "run_child",
        subagent: "reviewer",
        status: "completed",
        output: "**INC-118** matches this fold.",
        failure: null,
      },
    }),
    thread("new"),
  );
  expect(request).toEqual({
    kind: "subagent_result",
    subagent: "reviewer",
    status: "completed",
    text: "**INC-118** matches this fold.",
  });
});

it("reports an unsuccessful child result without inventing a payload", () => {
  expect(
    runRequest(
      run({
        trigger: "child_result",
        input: { status: "failed", output: null },
      }),
    ),
  ).toEqual({
    kind: "subagent_result",
    subagent: null,
    status: "failed",
    text: "",
  });
});

it("reads a child Thread's Run as the task its parent delegated", () => {
  const request = runRequest(
    run({
      input: message(
        JSON.stringify({
          delegated_task: "Find prior incidents for cursor folds",
          parent_task: "Run the checks and fix whatever fails",
          parent_history_summary: "…",
        }),
      ),
    }),
    thread("child"),
  );
  expect(request).toEqual({
    kind: "delegated_task",
    text: "Find prior incidents for cursor folds",
    parentTask: "Run the checks and fix whatever fails",
  });
});

it("shows a structured delegated task compactly and keeps plain text a message", () => {
  expect(
    runRequest(
      run({
        input: message(JSON.stringify({ delegated_task: { area: "docs" } })),
      }),
      thread("child"),
    ),
  ).toMatchObject({
    kind: "delegated_task",
    text: '{"area":"docs"}',
    parentTask: null,
  });
  // A child Run whose input is not the envelope stays what it reads as.
  expect(
    runRequest(run({ input: message("Just do it") }), thread("child")),
  ).toMatchObject({ kind: "message", text: "Just do it" });
  // The envelope is only read for a child Thread.
  expect(
    runRequest(
      run({ input: message('{"delegated_task":"x"}') }),
      thread("new"),
    ),
  ).toMatchObject({ kind: "message" });
});
