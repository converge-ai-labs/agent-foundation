import { afterEach, expect, it, vi } from "vitest";
import { mockWebSocket } from "../../tests/fake-websocket";
import { createTransport } from "../transport/client";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
import * as Y from "yjs";
import { covers, encode, replica, ThreadDraft, values } from "./draft";
import { attachmentToken } from "./inline-attachments";

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
    const selected = draft.addAttachment("file-old");
    draft.receive(frame(draft.doc));
    const capture = draft.capture();
    const peer = replica(Y.encodeStateAsUpdate(draft.doc));
    peer.getText("text").insert(0, "NEXT");
    peer.getMap("attachments").set(selected, "file-new");
    const extra = `inline-${crypto.randomUUID()}`;
    peer.getMap("attachments").set(extra, "file-extra");
    peer
      .getText("text")
      .insert(0, attachmentToken(extra) + attachmentToken(selected));
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

it("keeps upload completion out of native undo and submits authored attachment order", () => {
  const draft = new ThreadDraft();
  draft.doc.getText("text").insert(0, "before after");
  const key = draft.addAttachment("pending", 7);
  draft.receive(frame(draft.doc));
  expect(() => draft.capture()).toThrow("incomplete attachments");
  draft.doc.getMap("attachments").set(key, "attachment-image");
  draft.undo.undo();
  expect(values(draft.doc)).toEqual({
    prompt: "before after",
    attachment_ids: [],
  });
  draft.undo.redo();
  draft.receive(frame(draft.doc));
  const capture = draft.capture();
  expect(capture.parts).toEqual([
    "before ",
    { attachment_id: "attachment-image" },
    "after",
  ]);
  capture.doc.destroy();
});

it.each([true, false])(
  "clears captured inline identities, preserving concurrent attachments (%s)",
  (incomingFirst) => {
    const draft = new ThreadDraft();
    draft.doc.getText("text").insert(0, "submitted");
    draft.addAttachment("attachment-old", 3);
    draft.receive(frame(draft.doc));
    const captured = draft.capture();
    const peer = new ThreadDraft();
    Y.applyUpdate(peer.doc, Y.encodeStateAsUpdate(draft.doc));
    peer.addAttachment("attachment-new", 0);
    if (incomingFirst) draft.receive(frame(peer.doc));
    draft.clear(captured);
    if (!incomingFirst) draft.receive(frame(peer.doc));
    expect(values(draft.doc)).toEqual({
      prompt: "",
      attachment_ids: ["attachment-new"],
    });
    expect(captured.parts).toEqual([
      "sub",
      { attachment_id: "attachment-old" },
      "mitted",
    ]);
    captured.doc.destroy();
  },
);

it("counts only live tokens while retaining deleted registry entries for undo", () => {
  const draft = new ThreadDraft();
  for (let index = 0; index < 12; index++) {
    const key = draft.addAttachment(`attachment-${index}`);
    draft.removeAttachment(key);
  }
  expect(values(draft.doc).attachment_ids).toEqual([]);
  draft.undo.undo();
  expect(values(draft.doc).attachment_ids).toEqual(["attachment-11"]);
  for (let index = 0; index < 7; index++)
    draft.addAttachment(`attachment-new-${index}`);
  expect(() => draft.addAttachment("ninth")).toThrow("eight");
});

it.each([true, false])(
  "retains registry identity for an uncaptured pasted occurrence (%s)",
  (incomingFirst) => {
    const draft = new ThreadDraft();
    const key = draft.addAttachment("attachment-shared");
    draft.receive(frame(draft.doc));
    const captured = draft.capture();
    const peer = replica(Y.encodeStateAsUpdate(draft.doc));
    peer
      .getText("text")
      .insert(peer.getText("text").length, `\ufffc${key}\ufffc`);
    if (incomingFirst) draft.receive(frame(peer));
    draft.clear(captured);
    if (!incomingFirst) draft.receive(frame(peer));
    draft.receive(frame(draft.doc));
    const next = draft.capture();
    expect(next.parts).toEqual([{ attachment_id: "attachment-shared" }]);
    expect(values(draft.doc).attachment_ids).toEqual(["attachment-shared"]);
    captured.doc.destroy();
    next.doc.destroy();
    peer.destroy();
  },
);

it("does not decode repeated presence-only frames or acknowledge local edits with them", () => {
  const draft = new ThreadDraft();
  const server = replica();
  server.getText("text").insert(0, "shared");
  const accepted = frame(server);
  draft.receive(accepted);
  draft.doc.getText("text").insert(0, "local ");
  const decode = vi.spyOn(globalThis, "atob");
  draft.receive({
    ...accepted,
    participants: { peer: { name: "Peer", color: "#112233" } },
  });
  expect(decode).not.toHaveBeenCalled();
  expect(draft.participants.peer.name).toBe("Peer");
  expect(draft.synchronized).toBe(false);
  // Even byte-identical updates must initialize a replacement document.
  draft.receive({ ...accepted, draft_id: "draft-two" });
  draft.joinReplacement(false);
  expect(decode).toHaveBeenCalledTimes(1);
  expect(values(draft.doc).prompt).toBe("shared");
  expect(draft.synchronized).toBe(true);
});

it("publishes attachment registry and token in one update without changing undo", () => {
  const draft = new ThreadDraft();
  const updates = vi.fn();
  draft.doc.on("update", updates);
  draft.addAttachment("attachment-one");
  expect(updates).toHaveBeenCalledTimes(1);
  draft.undo.undo();
  expect(values(draft.doc).attachment_ids).toEqual([]);
  draft.undo.redo();
  expect(values(draft.doc).attachment_ids).toEqual(["attachment-one"]);
});

it.each([false, true])(
  "replays the latest pre-join presence and only sends uncovered edits (%s)",
  (offlineEdit) => {
    vi.useFakeTimers();
    vi.stubGlobal("window", { location: { origin: "http://localhost" } });
    const socket = mockWebSocket();
    const draft = new ThreadDraft();
    if (offlineEdit) draft.doc.getText("text").insert(0, "offline");
    const connection = draft.connect(
      createTransport("key", () => {}),
      "thread-one",
      () => {},
    );
    connection.presence({
      name: "Old",
      color: "#112233",
      anchor: "YQ==",
      head: "Yg==",
    });
    connection.presence({
      name: "Alice",
      color: "#112233",
      anchor: null,
      head: null,
    });
    const ws = socket();
    ws.open();
    ws.message({ kind: "draft", ...frame(replica()) });
    expect(ws.sent.filter((item) => item.kind === "sync")).toHaveLength(
      offlineEdit ? 1 : 0,
    );
    expect(ws.sent.filter((item) => item.kind === "presence")).toEqual([
      {
        kind: "presence",
        draft_id: "draft-one",
        presence: { name: "Alice", color: "#112233", anchor: null, head: null },
      },
    ]);
    connection.presence({
      name: "Alice",
      color: "#112233",
      anchor: "YQ==",
      head: "Yg==",
    });
    ws.close();
    vi.advanceTimersByTime(1000);
    socket().open();
    socket().message({ kind: "draft", ...frame(replica()) });
    expect(
      socket().sent.find((item) => item.kind === "presence")?.presence,
    ).toEqual({ name: "Alice", color: "#112233", anchor: null, head: null });
    connection.close();
  },
);

it("does not revive an expired pre-join selection", () => {
  vi.useFakeTimers();
  vi.stubGlobal("window", { location: { origin: "http://localhost" } });
  const socket = mockWebSocket();
  const draft = new ThreadDraft();
  const connection = draft.connect(
    createTransport("key", () => {}),
    "thread-one",
    () => {},
  );
  connection.presence({
    name: "Alice",
    color: "#112233",
    anchor: "YQ==",
    head: "Yg==",
  });
  vi.advanceTimersByTime(30000);
  socket().open();
  socket().message({ kind: "draft", ...frame(replica()) });
  expect(
    socket().sent.find((item) => item.kind === "presence")?.presence,
  ).toEqual({ name: "Alice", color: "#112233", anchor: null, head: null });
  connection.close();
});
