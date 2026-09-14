import { expect, it } from "vitest";
import * as Y from "yjs";
import { covers, encode, replica, ThreadDraft, values } from "./draft";

function frame(doc: Y.Doc, draft_id = "draft-one") {
  return {
    draft_id,
    participant_id: "participant-one",
    participants: {},
    update_base64: encode(Y.encodeStateAsUpdate(doc)),
  };
}
it("requires server coverage of deletions even when state vectors are unchanged", () => {
  const draft = new ThreadDraft();
  draft.doc.getText("text").insert(0, "hello");
  const server = replica(Y.encodeStateAsUpdate(draft.doc));
  draft.receive(frame(server));
  expect(draft.synchronized).toBe(true);
  draft.doc.getText("text").delete(0, 1);
  expect(Y.encodeStateVector(draft.doc)).toEqual(Y.encodeStateVector(server));
  expect(draft.synchronized).toBe(false);
  expect(() => draft.capture()).toThrow("Synchronize");
  Y.applyUpdate(server, Y.encodeStateAsUpdate(draft.doc));
  draft.receive(frame(server));
  expect(draft.synchronized).toBe(true);
});
for (const incomingFirst of [true, false])
  it(`clears captured identities, preserving concurrent text and replacement selections (${incomingFirst})`, () => {
    const draft = new ThreadDraft();
    draft.doc.getText("text").insert(0, "submitted");
    draft.doc.getMap("attachments").set("selection", "file-old");
    draft.receive(frame(draft.doc));
    const capture = draft.capture();
    const peer = replica(Y.encodeStateAsUpdate(draft.doc));
    peer.getText("text").insert(0, "NEXT");
    peer.getMap("attachments").set("selection", "file-new");
    peer.getMap("attachments").set("extra", "file-extra");
    if (incomingFirst) draft.receive(frame(peer));
    draft.clear(capture);
    if (!incomingFirst) draft.receive(frame(peer));
    expect(values(draft.doc)).toEqual({
      prompt: "NEXT",
      attachment_ids: ["file-extra", "file-new"],
    });
    expect(capture.input).toEqual({
      prompt: "submitted",
      attachment_ids: ["file-old"],
    });
  });
it("local undo never removes a remote participant's edit", () => {
  const draft = new ThreadDraft();
  draft.doc.getText("text").insert(0, "local");
  const peer = replica(Y.encodeStateAsUpdate(draft.doc));
  peer.getText("text").insert(0, "remote");
  draft.receive(frame(peer));
  draft.undo.undo();
  expect(values(draft.doc).prompt).toBe("remote");
});
it("requires explicit recovery after an incarnation change and never sends an old clear", () => {
  const draft = new ThreadDraft();
  draft.doc.getText("text").insert(0, "old text");
  draft.receive(frame(draft.doc));
  const capture = draft.capture();
  const replacement = replica();
  replacement.getText("text").insert(0, "new shared text ");
  draft.receive(frame(replacement, "draft-two"));
  expect(values(draft.doc).prompt).toBe("old text");
  expect(draft.synchronized).toBe(false);
  draft.joinReplacement(true);
  draft.clear(capture);
  expect(values(draft.doc).prompt).toBe("new shared text old text");
  expect(draft.synchronized).toBe(false);
});
it("compares deletion coverage rather than visible text equality", () => {
  const a = replica(),
    b = replica();
  a.getText("text").insert(0, "same");
  b.getText("text").insert(0, "same");
  expect(covers(Y.snapshot(a), Y.snapshot(b))).toBe(false);
});
