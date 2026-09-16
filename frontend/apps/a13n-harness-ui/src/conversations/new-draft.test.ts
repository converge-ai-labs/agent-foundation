// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as Y from "yjs";
import { ThreadDraft, values } from "./draft";
import { NewDraftStore } from "./new-draft";

const stores: NewDraftStore[] = [];
const composers = new Map<string, ThreadDraft>();
function store() {
  const next = new NewDraftStore();
  stores.push(next);
  return next;
}
beforeEach(() => localStorage.clear());
afterEach(() => {
  for (const item of stores.splice(0)) item.dispose();
  for (const composer of composers.values()) {
    composer.undo.destroy();
    composer.doc.destroy();
  }
  composers.clear();
  vi.restoreAllMocks();
});

it("persists only one slot and registers its recovered composer before saved Thread routes mount", () => {
  const original = store().get(composers);
  original.defaults = {
    project_id: "project-one",
    agent_id: "writer",
    environment_profile_id: "local",
  };
  original.composer.doc.getText("text").insert(0, "Keep this");
  original.composer.draftId = "shared-incarnation";
  original.composer.modelId = "chosen-model";
  original.created = true;
  original.attempted = true;
  original.composer.submission = { kind: "pending", action: "send" };
  original.composer.notify();
  const recovered = store().get(composers);
  expect(localStorage.length).toBe(1);
  expect(recovered.threadId).toBe(original.threadId);
  expect(recovered.defaults).toEqual(original.defaults);
  expect(composers.get(original.threadId)).toBe(recovered.composer);
  expect(recovered.composer.submission.kind).toBe("unknown");
  expect(recovered.composer.draftId).toBe("shared-incarnation");
  expect(recovered.composer.modelId).toBe("chosen-model");
  expect(values(recovered.composer.doc).prompt).toBe("Keep this");
  // Restoring the same Yjs identities must not duplicate text on reconnect.
  Y.applyUpdate(
    recovered.composer.doc,
    Y.encodeStateAsUpdate(original.composer.doc),
  );
  expect(values(recovered.composer.doc).prompt).toBe("Keep this");
});

it("clears an accepted slot even after leaving New and never lets the old composer overwrite its replacement", () => {
  const owner = store();
  const first = owner.get(composers);
  first.composer.doc.getText("text").insert(0, "First");
  first.composer.submission = {
    kind: "accepted",
    receipt: "receipt-one",
    message: "Accepted",
  };
  first.composer.notify();
  expect(localStorage.getItem("a13n-harness-ui.new-draft")).toBeNull();
  const second = owner.get(composers);
  expect(second.threadId).not.toBe(first.threadId);
  second.composer.doc.getText("text").insert(0, "Second");
  first.composer.doc.getText("text").insert(0, "Old edits");
  expect(values(store().get(composers).composer.doc).prompt).toBe("Second");
});

it.each([
  "{broken",
  "null",
  '{"version":99}',
  '{"version":1,"threadId":"invalid"}',
])("ignores malformed browser storage: %s", (raw) => {
  localStorage.setItem("a13n-harness-ui.new-draft", raw);
  const draft = store().get(composers);
  expect(draft.threadId).toMatch(/^thread_[0-9a-f]{32}$/);
  expect(values(draft.composer.doc).prompt).toBe("");
  draft.composer.doc.getText("text").insert(0, "New text");
  expect(values(store().get(composers).composer.doc).prompt).toBe("New text");
});

it("keeps composition usable and discloses failed persistence", () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("Quota exceeded", "QuotaExceededError");
  });
  const owner = store();
  const draft = owner.get(composers);
  draft.composer.doc.getText("text").insert(0, "Keep this tab");
  expect(owner.error).toContain("Keep this tab open");
  expect(values(owner.get(composers).composer.doc).prompt).toBe(
    "Keep this tab",
  );
  vi.restoreAllMocks();
  draft.save();
  expect(owner.error).toBe("");
  expect(values(store().get(composers).composer.doc).prompt).toBe(
    "Keep this tab",
  );
});

it("does not reuse a completed Thread when storage cleanup is unavailable", () => {
  const owner = store();
  const first = owner.get(composers);
  first.composer.doc.getText("text").insert(0, "First");
  vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
    throw new Error("Storage unavailable");
  });
  first.composer.submission = {
    kind: "accepted",
    receipt: "receipt-one",
    message: "Accepted",
  };
  first.composer.notify();
  expect(owner.get(composers).threadId).not.toBe(first.threadId);
});

it("detaches an archived failed Thread without losing text or reusing its attachment handles", () => {
  const owner = store();
  const old = owner.get(composers);
  old.created = true;
  old.attempted = true;
  old.composer.doc.getText("text").insert(0, "Keep this");
  const attachment = old.composer.addAttachment("attachment-one");
  old.composer.submission = { kind: "rejected", message: "Admission rejected" };
  owner.detachArchived(old.threadId);
  const next = owner.get(composers);
  expect(next.threadId).not.toBe(old.threadId);
  expect(next.created).toBe(false);
  expect(next.attempted).toBe(false);
  expect(next.composer.submission.kind).toBe("idle");
  expect(values(next.composer.doc).prompt).toBe("Keep this");
  expect(next.composer.doc.getMap("attachments").get(attachment)).toBe(
    "failed",
  );
  expect(old.composer.doc.getMap("attachments").get(attachment)).toBe(
    "attachment-one",
  );
  expect(store().get(composers).threadId).toBe(next.threadId);
});

it("does not treat archiving as acknowledgement of an unknown submission", () => {
  const owner = store();
  const old = owner.get(composers);
  old.composer.submission = { kind: "pending", action: "send" };
  owner.detachArchived("another-thread");
  expect(owner.current).toBe(old);
  owner.detachArchived(old.threadId);
  expect(owner.current!.composer.submission.kind).toBe("unknown");
});
