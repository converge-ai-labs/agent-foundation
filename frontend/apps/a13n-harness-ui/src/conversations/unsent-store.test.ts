import { expect, it, vi } from "vitest";
import * as Y from "yjs";
import type { Schema } from "../transport/client";
import { ThreadDraft, encode } from "./draft";
import { UnsentStore, unsentInputs } from "./unsent-store";

function acknowledge(draft: ThreadDraft) {
  draft.receive({
    kind: "draft",
    draft_id: "draft-one",
    participant_id: "me",
    update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
    participants: {},
    closed: false,
  });
}
const summary = (
  id: string,
  since = "2026-09-21T10:00:00Z",
): Schema<"DraftSummary"> => ({
  thread_id: id,
  draft_id: "draft-one",
  unsent_since: since,
});

it("tracks meaningful input, including incomplete attachments, without reordering on each edit", () => {
  const draft = new ThreadDraft();
  const store = new UnsentStore();
  store.track("one", draft);
  const notify = vi.fn();
  store.subscribe(notify);
  draft.doc.getText("text").insert(0, " \n");
  expect(unsentInputs([], store.getSnapshot()).size).toBe(0);
  draft.mode = "goal";
  draft.notify();
  expect(unsentInputs([], store.getSnapshot()).size).toBe(0);
  const key = draft.addAttachment("pending");
  const first = unsentInputs([], store.getSnapshot()).get("one");
  expect(first).toBeTruthy();
  const calls = notify.mock.calls.length;
  draft.doc.getMap("attachments").set(key, "failed");
  draft.doc.getText("text").insert(0, "Still editing");
  expect(unsentInputs([], store.getSnapshot()).get("one")).toBe(first);
  expect(notify).toHaveBeenCalledTimes(calls);
  draft.doc.getText("text").delete(0, draft.doc.getText("text").length);
  // The retained inline registry is undo history, not selected input.
  expect(draft.doc.getMap("attachments").size).toBe(1);
  expect(unsentInputs([], store.getSnapshot()).size).toBe(0);
  store.dispose();
});

it("lets server discovery replace clean detached replicas but retains offline edits and deletions", () => {
  const draft = new ThreadDraft();
  const store = new UnsentStore();
  store.track("one", draft);
  draft.doc.getText("text").insert(0, "Shared input");
  acknowledge(draft);
  draft.status = "Disconnected";
  draft.notify();
  expect(unsentInputs([summary("one")], store.getSnapshot()).has("one")).toBe(
    true,
  );
  // A peer cleared the room while this editor was closed.
  expect(unsentInputs([], store.getSnapshot()).has("one")).toBe(false);
  draft.doc.getText("text").insert(0, "Offline change");
  expect(unsentInputs([], store.getSnapshot()).has("one")).toBe(true);
  draft.doc.getText("text").delete(0, draft.doc.getText("text").length);
  expect(unsentInputs([summary("one")], store.getSnapshot()).has("one")).toBe(
    false,
  );
  store.dispose();
});

it("discovers unvisited rooms and preserves concurrent input after clearing an accepted capture", () => {
  const draft = new ThreadDraft();
  const store = new UnsentStore();
  store.track("one", draft);
  draft.doc.getText("text").insert(0, "Send me");
  acknowledge(draft);
  const captured = draft.capture();
  draft.doc.getText("text").insert(0, "Next input");
  draft.clear(captured);
  captured.doc.destroy();
  expect(draft.hasUnsentInput).toBe(true);
  acknowledge(draft);
  const next = draft.capture();
  draft.clear(next);
  next.doc.destroy();
  expect(draft.hasUnsentInput).toBe(false);
  expect([
    ...unsentInputs(
      [summary("old"), summary("new", "2026-09-21T11:00:00Z")],
      store.getSnapshot(),
    ).keys(),
  ]).toEqual(["new", "old"]);
  store.dispose();
});

it.each([false, true])(
  "does not hide a replacement room behind an old empty replica (offline deletion: %s)",
  (deleted) => {
    const draft = new ThreadDraft();
    const store = new UnsentStore();
    store.track("one", draft);
    if (deleted) draft.doc.getText("text").insert(0, "Old input");
    acknowledge(draft);
    draft.status = "Disconnected";
    if (deleted)
      draft.doc.getText("text").delete(0, draft.doc.getText("text").length);
    draft.notify();
    const shared = [{ ...summary("one"), draft_id: "replacement" }];
    // Summary discovery can learn the new incarnation before the editor rejoins.
    expect(unsentInputs(shared, store.getSnapshot()).has("one")).toBe(true);
    const peer = new ThreadDraft();
    peer.doc.getText("text").insert(0, "New room input");
    draft.receive({
      kind: "draft",
      draft_id: "replacement",
      participant_id: "me",
      update_base64: encode(Y.encodeStateAsUpdate(peer.doc)),
      participants: {},
      closed: false,
    });
    draft.status = "Disconnected";
    draft.notify();
    expect(draft.hasUnsentInput).toBe(false);
    expect(unsentInputs(shared, store.getSnapshot()).has("one")).toBe(true);
    // Retained nonempty input still needs explicit recovery, even with an empty index.
    draft.doc.getText("text").insert(0, "Recover this");
    expect(unsentInputs([], store.getSnapshot()).has("one")).toBe(true);
    store.dispose();
  },
);
