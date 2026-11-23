// @vitest-environment jsdom
import { useSyncExternalStore } from "react";
import { act, renderHook } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import * as Y from "yjs";
import type { Transport } from "../transport/client";
import { encode, optionsReplica, ThreadDraft } from "./draft";
import { useInputPrediction } from "./input-prediction";

function mount(draft: ThreadDraft) {
  const post = vi.fn(() => new Promise(() => {}));
  const transport = { client: { POST: post } } as unknown as Transport;
  const hook = renderHook(
    ({ head, enabled }) => {
      useSyncExternalStore(draft.subscribe, draft.getSnapshot);
      return useInputPrediction(transport, draft, "thread-1", head, enabled);
    },
    { initialProps: { head: "old", enabled: true } },
  );
  return { ...hook, post };
}

it("reuses an acknowledged options prediction, ignores its history for legacy choices, and dismisses only that continuation", () => {
  const draft = new ThreadDraft();
  const options = optionsReplica();
  options.clientID = 1;
  const map = options.getMap("options");
  function receive() {
    draft.receive({
      kind: "draft",
      draft_id: "draft-1",
      participant_id: "participant-1",
      participants: {},
      update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
      options_update_base64: encode(Y.encodeStateAsUpdate(options)),
    });
  }
  map.set("prediction", { continuation_id: "old", text: "Add an example." });
  receive();
  const { result, post, rerender } = mount(draft);
  expect(result.current.prediction?.text).toBe("Add an example.");
  expect(draft.hasUnsentInput).toBe(false);
  expect(draft.synchronized).toBe(true);
  expect(draft.optionsUninitialized).toBe(true);
  expect(post).not.toHaveBeenCalled();
  act(() => result.current.prediction?.dismiss());
  expect(result.current.prediction).toBeUndefined();
  expect(post).not.toHaveBeenCalled();
  act(() => {
    map.delete("prediction");
    receive();
  });
  expect(draft.optionsUninitialized).toBe(true);
  act(() => {
    map.set("prediction", {
      continuation_id: "next",
      text: "Continue with step two.",
    });
    receive();
  });
  rerender({ head: "next", enabled: true });
  expect(result.current.prediction?.text).toBe("Continue with step two.");
  expect(post).not.toHaveBeenCalled();
  rerender({ head: "next", enabled: false });
  expect(result.current.prediction).toBeUndefined();
});

it("aborts the pull immediately when a collaborator inserts text", () => {
  const draft = new ThreadDraft();
  const { post, result } = mount(draft);
  expect(post).toHaveBeenCalledTimes(1);
  const signal = (
    post.mock.calls as unknown as [string, { signal: AbortSignal }][]
  )[0][1].signal;
  act(() => draft.doc.getText("text").insert(0, "Peer wrote this"));
  expect(signal.aborted).toBe(true);
  expect(result.current.prediction).toBeUndefined();
});
