import { expect, it } from "vitest";
import type { Schema } from "../../shared/api";
import { runRequest } from "./request";

const run = (overrides: Partial<Schema["RunResource"]>) =>
  ({
    id: "run_1",
    input_kind: "agent_input",
    input: null,
    input_text: null,
    ...overrides,
  }) as Schema["RunResource"];

const thread = (role: string) =>
  ({ id: "thr_1", role }) as Schema["ThreadResource"];

const message = (text: string) => ({
  schema_version: "2",
  content: [{ type: "text", text }],
});

it("reads ordinary input as the message a person sent", () => {
  const request = runRequest(
    run({
      input: message("Review the release"),
      input_text: "Review the release",
    }),
    thread("root"),
  );
  expect(request).toMatchObject({
    kind: "message",
    text: "Review the release",
  });
});

it("keeps a waiting resolution as feedback and its input alongside", () => {
  const input = {
    schema_version: "1",
    waiting_run_id: "run_0",
    resolutions: [{ action: "approve", call_id: "call_1" }],
  };
  expect(
    runRequest(run({ input_kind: "waiting_feedback", input }), thread("root")),
  ).toMatchObject({ kind: "feedback", input });
});

it("reads a default continuation as the message it carried", () => {
  const request = runRequest(
    run({
      input_kind: "waiting_continue",
      input: {
        schema_version: "1",
        waiting_run_id: "run_0",
        resolutions: [],
        input: message("Carry on without it"),
      },
    }),
    thread("root"),
  );
  expect(request).toMatchObject({
    kind: "continue",
    text: "Carry on without it",
  });
});

it("reads an asynchronous child's result as prose, not as its envelope", () => {
  const request = runRequest(
    run({
      input_kind: "async_subagent_result",
      input: {
        schema_version: "1",
        relationship_id: "crr_1",
        subagent_name: "reviewer",
        child_thread_id: "thr_child",
        child_run_id: "run_child",
        terminal_status: "completed",
        result_payload: "**INC-118** matches this fold.",
      },
    }),
    thread("root"),
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
        input_kind: "async_subagent_result",
        input: { terminal_status: "failed", result_payload: null },
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
      thread("root"),
    ),
  ).toMatchObject({ kind: "message" });
});
