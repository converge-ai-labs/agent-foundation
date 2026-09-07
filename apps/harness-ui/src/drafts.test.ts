import { describe, expect, it } from "vitest";
import { emptyDraft, updateDrafts, type Drafts } from "./drafts";

describe("tab-local submission acknowledgements", () => {
  it("retains new-conversation selectors independently from a dispatched revision", () => {
    const submission = { source: "new", text: "A", revision: 0 };
    const selection = {
      project_id: "project-b",
      agent_id: "agent-grok",
      environment_profile_id: "environment-sandbox",
    };
    let state: Drafts = updateDrafts(
      { new: { ...emptyDraft, text: "A" } },
      { kind: "selection", id: "new", selection },
    );
    state = updateDrafts(state, {
      kind: "created",
      submission,
      target: "thread-a",
    });
    state = updateDrafts(state, {
      kind: "finish",
      submission,
      target: "thread-a",
    });
    expect(state.new.selection).toEqual(selection);
    expect(state.new.text).toBe("");
  });

  it("preserves newer text when an existing submission completes", () => {
    const submission = { source: "thread-a", text: "A", revision: 1 };
    let state: Drafts = {
      "thread-a": { ...emptyDraft, text: "A", revision: 1 },
    };
    state = updateDrafts(state, { kind: "begin", submission });
    state = updateDrafts(state, { kind: "edit", id: "thread-a", text: "B" });
    state = updateDrafts(state, {
      kind: "finish",
      submission,
      target: "thread-a",
      receipt: "receipt-a",
    });
    expect(state["thread-a"]).toMatchObject({
      text: "B",
      pending: false,
      receipt: "receipt-a",
    });
  });
  it("transfers only the submitted revision into a created thread and retains unknown outcomes", () => {
    const submission = { source: "new", text: "A", revision: 1 };
    let state: Drafts = { new: { ...emptyDraft, text: "A", revision: 1 } };
    state = updateDrafts(state, { kind: "begin", submission });
    state = updateDrafts(state, { kind: "edit", id: "new", text: "B" });
    state = updateDrafts(state, {
      kind: "created",
      submission,
      target: "thread-a",
    });
    expect(state.new).toMatchObject({ text: "B", pending: true });
    expect(state["thread-a"]).toMatchObject({ text: "A", pending: true });
    state = updateDrafts(state, {
      kind: "finish",
      submission,
      target: "thread-a",
      error: "Outcome unknown",
      uncertain: true,
    });
    expect(state.new).toMatchObject({ text: "B", pending: false });
    expect(state["thread-a"]).toMatchObject({
      text: "A",
      pending: false,
      uncertain: true,
    });
    state = updateDrafts(state, { kind: "acknowledge", id: "thread-a" });
    expect(state["thread-a"]).toMatchObject({
      text: "A",
      uncertain: false,
      error: "",
    });
  });
  it("does not delete new text edited after creation or another thread's draft", () => {
    const submission = { source: "new", text: "A", revision: 1 };
    let state: Drafts = {
      new: { ...emptyDraft, text: "A", revision: 1 },
      "thread-b": { ...emptyDraft, text: "Unrelated" },
    };
    state = updateDrafts(state, {
      kind: "created",
      submission,
      target: "thread-a",
    });
    state = updateDrafts(state, { kind: "edit", id: "new", text: "B" });
    state = updateDrafts(state, { kind: "edit", id: "thread-a", text: "C" });
    state = updateDrafts(state, {
      kind: "finish",
      submission,
      target: "thread-a",
      receipt: "receipt-a",
    });
    expect(state.new.text).toBe("B");
    expect(state["thread-a"].text).toBe("C");
    expect(state["thread-b"].text).toBe("Unrelated");
  });
  it("retains a rejected steering prompt without treating it as a lost response", () => {
    const submission = { source: "thread-a", text: "A", revision: 0 };
    const state = updateDrafts(
      { "thread-a": { ...emptyDraft, text: "A" } },
      {
        kind: "finish",
        submission,
        target: "thread-a",
        error: "Steering rejected",
      },
    );
    expect(state["thread-a"]).toMatchObject({
      text: "A",
      uncertain: false,
      error: "Steering rejected",
    });
  });
});
