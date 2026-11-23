// @vitest-environment jsdom
import { useSyncExternalStore } from "react";
import { act, renderHook } from "@testing-library/react";
import { expect, it } from "vitest";
import * as Y from "yjs";
import { encode, optionsReplica, ThreadDraft } from "./draft";
import { useInputPrediction } from "./input-prediction";

function mount(draft: ThreadDraft) {
  return renderHook(
    ({ head, enabled }) => {
      useSyncExternalStore(draft.subscribe, draft.getSnapshot);
      return useInputPrediction(draft, head, enabled);
    },
    { initialProps: { head: "old", enabled: true } },
  );
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
  const { result, rerender } = mount(draft);
  expect(result.current.prediction?.text).toBe("Add an example.");
  expect(draft.hasUnsentInput).toBe(false);
  expect(draft.synchronized).toBe(true);
  expect(draft.optionsUninitialized).toBe(true);
  act(() => result.current.prediction?.dismiss());
  expect(result.current.prediction).toBeUndefined();
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
  rerender({ head: "next", enabled: false });
  expect(result.current.prediction).toBeUndefined();
});

it("shows a pushed suggestion only while the composer is empty and not composing", () => {
  const draft = new ThreadDraft();
  const { result } = mount(draft);
  expect(result.current.prediction).toBeUndefined();
  act(() =>
    draft.receive({
      kind: "draft",
      draft_id: "draft-1",
      participant_id: "participant-1",
      participants: {},
      update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
      prediction: { continuation_id: "old", text: "Add an example." },
    }),
  );
  expect(result.current.prediction?.text).toBe("Add an example.");
  act(() => result.current.setComposing(true));
  expect(result.current.prediction).toBeUndefined();
  act(() => result.current.setComposing(false));
  act(() => draft.doc.getText("text").insert(0, "Peer wrote this"));
  expect(result.current.prediction).toBeUndefined();
  act(() => draft.doc.getText("text").delete(0, "Peer wrote this".length));
  expect(result.current.prediction?.text).toBe("Add an example.");
});
