import { expect, it } from "vitest";
import { entryResubmission, messagePayload, runResubmission } from "./resubmit";
import { fixtureRun, textInput } from "./transcript/fixture";

it("resubmits a stopped Run's message with its options and only a chosen pin", () => {
  const options = {
    labels: { purpose: "check" },
    max_usage: { requests: 4 },
    overrides: { instructions: "Answer briefly." },
  };
  expect(
    runResubmission(
      fixtureRun({
        status: "failed",
        revision_selection: "pinned",
        options,
      }),
    ),
  ).toEqual({
    payload: { content: [{ type: "text", text: "Run the checks" }] },
    agent_revision_id: "rev_1",
    options,
  });
  // A Run on the default revision starts again on whatever is default then.
  expect(
    runResubmission(fixtureRun({ status: "failed" }))?.agent_revision_id,
  ).toBeNull();
});

it("sends a pending message again with the options it was accepted with", () => {
  const options = {
    labels: { purpose: "check" },
    overrides: { model_settings: { max_tokens: 512 } },
  };
  expect(
    entryResubmission({
      id: "inb_1",
      thread_id: "thr_1",
      kind: "message",
      delivery: "next_run",
      position: 1,
      status: "pending",
      principal_id: "usr_1",
      payload: textInput("Run the checks"),
      agent_id: "agt_1",
      agent_revision_id: null,
      options,
      child_run_id: null,
      origin_run_id: null,
      assigned_run_id: null,
      incorporated_checkpoint_seq: null,
      failure: null,
      created_at: "2026-09-20T10:00:00.000Z",
      finished_at: null,
    }),
  ).toEqual({
    payload: textInput("Run the checks"),
    agent_id: "agt_1",
    agent_revision_id: null,
    delivery: "next_run",
    options,
  });
});

it("never resubmits what a message did not start", () => {
  expect(
    runResubmission(
      fixtureRun({ trigger: "resume", input: null, status: "failed" }),
    ),
  ).toBeNull();
  expect(
    runResubmission(
      fixtureRun({
        trigger: "child_result",
        input: { child_run_id: "run_child", status: "completed" },
      }),
    ),
  ).toBeNull();
  expect(
    entryResubmission({
      id: "inb_1",
      thread_id: "thr_1",
      kind: "child_result",
      delivery: "next_run",
      position: 1,
      status: "pending",
      principal_id: "usr_1",
      payload: { child_run_id: "run_child", status: "completed" },
      agent_id: null,
      agent_revision_id: null,
      options: {},
      child_run_id: "run_child",
      origin_run_id: "run_2",
      assigned_run_id: null,
      incorporated_checkpoint_seq: null,
      failure: null,
      created_at: "2026-09-20T10:00:00.000Z",
      finished_at: null,
    }),
  ).toBeNull();
});

it("reads every part of a stored message and nothing that is not one", () => {
  const payload = {
    content: [
      { type: "text", text: "Compare" },
      { type: "asset", asset_id: "ast_0123456789abcdef0123" },
      { type: "url", url: "https://example.com/a.png" },
      { type: "json", value: { rows: 2 } },
    ],
  };
  expect(messagePayload(payload)).toEqual(payload);
  expect(messagePayload(textInput("Hello"))).toEqual(textInput("Hello"));
  expect(messagePayload({ content: [{ type: "binary" }] })).toBeNull();
  expect(messagePayload({ answers: [] })).toBeNull();
});
