import * as Y from "yjs";
import type { Schema, Transport } from "../transport/client";
import type { LocalInput } from "./local-input";
import {
  attachmentSelections,
  attachmentToken,
  inlinePattern,
  isReadyAttachment,
  orderedInput,
} from "./inline-attachments";

const remote = Symbol("server draft");
const captureClear = Symbol("accepted capture");
export function encode(bytes: Uint8Array): string {
  let value = "";
  for (const byte of bytes) value += String.fromCharCode(byte);
  return btoa(value);
}
export function decode(value: string): Uint8Array {
  return Uint8Array.from(atob(value), (character) => character.charCodeAt(0));
}
export function replica(update?: Uint8Array): Y.Doc {
  const doc = new Y.Doc();
  doc.getText("text");
  doc.getMap<string>("attachments");
  if (update) Y.applyUpdate(doc, update);
  return doc;
}
export function values(doc: Y.Doc) {
  return {
    prompt: doc.getText("text").toString().replace(inlinePattern, ""),
    attachment_ids: attachmentSelections(doc)
      .map(({ id }) => id)
      .filter(isReadyAttachment),
  };
}
// State vectors alone miss deletions. Compare the complete accepted snapshot,
// including deletion ranges, using Yjs public snapshot/update APIs.
export function covers(accepted: Y.Snapshot, local: Y.Snapshot): boolean {
  for (const [client, clock] of local.sv) {
    if ((accepted.sv.get(client) ?? 0) < clock) return false;
  }
  for (const [client, ranges] of local.ds.clients) {
    const received = accepted.ds.clients.get(client) ?? [];
    for (const range of ranges) {
      let end = range.clock;
      for (const item of received) {
        if (item.clock > end) break;
        end = Math.max(end, item.clock + item.len);
        if (end >= range.clock + range.len) break;
      }
      if (end < range.clock + range.len) return false;
    }
  }
  return true;
}
export type DraftCapture = {
  doc: Y.Doc;
  draftId: string;
  input: ReturnType<typeof values>;
  parts: ReturnType<typeof orderedInput>;
};
export type Submission =
  | { kind: "idle" }
  | { kind: "pending"; action: "send" | "steer" }
  | {
      kind: "unknown";
      action: "send" | "steer";
      message: string;
      receipt?: string;
    }
  | {
      kind: "accepted";
      action?: "send" | "steer";
      receipt: string;
    }
  | { kind: "rejected"; message: string };

export class ThreadDraft {
  doc = replica();
  undo = new Y.UndoManager(this.doc.getText("text"));
  // Retained IDs are a registry, not undoable editor content. In particular,
  // finishing an asynchronous upload must not revive a pending upload on undo.
  uploads = new Map<
    string,
    { file: File; status: "staged" | "pending" | "failed" }
  >();
  draftId: string | undefined;
  participantId: string | undefined;
  participants: Schema<"DraftFrame">["participants"] = {};
  status = "Disconnected";
  error = "";
  submission: Submission = { kind: "idle" };
  // Private presentation only. This is neither shared input nor an execution queue.
  localInputs: LocalInput[] = [];
  // A private, in-tab Send choice, not shared input or sticky Thread configuration.
  modelId: string | undefined;
  replacement: Schema<"DraftFrame"> | undefined;
  private accepted: Y.Snapshot | undefined;
  private listeners = new Set<() => void>();
  private version = 0;
  private send: (() => void) | undefined;
  private documentChanged = (_update: Uint8Array, origin: unknown) => {
    if (origin !== remote) {
      this.error = "";
      this.send?.();
    }
    this.notify();
  };
  constructor() {
    this.doc.on("update", this.documentChanged);
  }
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  getSnapshot = () => this.version;
  notify() {
    this.version++;
    this.listeners.forEach((listener) => listener());
  }
  get synchronized() {
    return (
      this.status === "Connected" &&
      !this.replacement &&
      !this.error &&
      !!this.accepted &&
      covers(this.accepted, Y.snapshot(this.doc))
    );
  }
  capture(): DraftCapture {
    if (!this.synchronized || !this.draftId)
      throw new Error("Synchronize your edits before sending.");
    const doc = replica(Y.encodeStateAsUpdate(this.doc));
    try {
      return {
        doc,
        draftId: this.draftId,
        input: values(doc),
        parts: orderedInput(doc),
      };
    } catch (error) {
      doc.destroy();
      throw error;
    }
  }
  clear(captured: DraftCapture) {
    // Never apply an old incarnation's deletion to a replacement draft.
    if (captured.draftId !== this.draftId || this.replacement) return;
    captured.doc.transact(() => {
      captured.doc
        .getText("text")
        .delete(0, captured.doc.getText("text").length);
      // Inline registry keys can also be used by uncaptured pasted occurrences,
      // including peer inserts that have not arrived yet. Keep those identities.
      const registry = captured.doc.getMap("attachments");
      for (const key of registry.keys())
        if (!key.startsWith("inline-")) registry.delete(key);
    });
    this.undo.stopCapturing();
    Y.applyUpdate(this.doc, Y.encodeStateAsUpdate(captured.doc), captureClear);
    this.undo.clear();
    for (const key of this.uploads.keys())
      if (!this.doc.getMap("attachments").has(key)) this.uploads.delete(key);
  }
  receive(frame: Schema<"DraftFrame">) {
    if (this.draftId && this.draftId !== frame.draft_id) {
      this.replacement = frame;
      this.status = "Server restarted";
      this.notify();
      return;
    }
    this.draftId = frame.draft_id;
    this.participantId = frame.participant_id;
    this.participants = frame.participants;
    const update = decode(frame.update_base64);
    const server = replica(update);
    this.accepted = Y.snapshot(server);
    server.destroy();
    Y.applyUpdate(this.doc, update, remote);
    this.status = frame.closed ? "Disconnected" : "Connected";
    this.notify();
  }
  joinReplacement(restore: boolean) {
    const frame = this.replacement;
    if (!frame) return;
    const oldText = this.doc.getText("text").toString();
    const oldAttachments = [
      ...this.doc.getMap<string>("attachments").entries(),
    ];
    this.doc.off("update", this.documentChanged);
    this.undo.destroy();
    this.doc.destroy();
    this.doc = replica();
    this.undo = new Y.UndoManager(this.doc.getText("text"));
    this.doc.on("update", this.documentChanged);
    this.draftId = undefined;
    this.replacement = undefined;
    this.error = "";
    this.receive(frame);
    if (restore)
      this.doc.transact(() => {
        this.doc
          .getText("text")
          .insert(this.doc.getText("text").length, oldText);
        for (const [key, id] of oldAttachments)
          this.doc.getMap("attachments").set(key, id);
      });
    this.send?.();
    this.notify();
  }
  addAttachment(id: string, at = this.doc.getText("text").length) {
    if (attachmentSelections(this.doc).length >= 8)
      throw new Error("Select up to eight attachments.");
    const key = `inline-${crypto.randomUUID()}`;
    this.doc.getMap("attachments").set(key, id);
    this.undo.stopCapturing();
    this.doc.getText("text").insert(at, attachmentToken(key));
    this.undo.stopCapturing();
    return key;
  }
  removeAttachment(key: string, at?: number) {
    const selection = attachmentSelections(this.doc).find(
      (item) => item.key === key && (at === undefined || item.from === at),
    );
    if (!selection) return;
    this.undo.stopCapturing();
    if (selection.from !== undefined)
      this.doc
        .getText("text")
        .delete(selection.from, selection.to! - selection.from);
    else this.doc.getMap("attachments").delete(key);
    this.undo.stopCapturing();
  }
  connect(transport: Transport, threadId: string, unauthorized: () => void) {
    let stopped = false;
    let socket: WebSocket | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let presenceExpiry: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    let joined = false;
    this.send = () => {
      if (
        !joined ||
        this.replacement ||
        !this.draftId ||
        socket?.readyState !== WebSocket.OPEN
      )
        return;
      const update = Y.encodeStateAsUpdate(this.doc);
      if (update.length > 512 * 1024) {
        this.error =
          "Draft exceeds the server's 512 KiB CRDT limit. Copy your text before rejoining a fresh draft.";
        this.notify();
        return;
      }
      socket.send(
        JSON.stringify({
          kind: "sync",
          draft_id: this.draftId,
          update_base64: encode(update),
        } satisfies Schema<"DraftCommand">),
      );
    };
    const connect = () => {
      joined = false;
      this.status = "Connecting";
      this.notify();
      const url = new URL(
        `/api/threads/${encodeURIComponent(threadId)}/draft/connect`,
        window.location.origin,
      );
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(url);
      socket = ws;
      ws.onopen = () => ws.send(JSON.stringify({ api_key: transport.key }));
      ws.onmessage = (event) => {
        if (stopped) return;
        try {
          const frame: unknown = JSON.parse(String(event.data));
          if (typeof frame !== "object" || frame === null)
            throw new Error("Invalid draft frame.");
          if ("error" in frame) {
            const error = frame.error;
            this.error =
              typeof error === "object" && error !== null && "message" in error
                ? String(error.message)
                : "Draft synchronization rejected.";
            this.notify();
            return;
          }
          if (
            !("kind" in frame) ||
            frame.kind !== "draft" ||
            !("draft_id" in frame) ||
            typeof frame.draft_id !== "string" ||
            !("update_base64" in frame) ||
            typeof frame.update_base64 !== "string" ||
            !("participants" in frame) ||
            typeof frame.participants !== "object"
          )
            throw new Error("Invalid draft frame.");
          const first = !joined;
          joined = true;
          this.receive(frame as Schema<"DraftFrame">);
          clearTimeout(presenceExpiry);
          // A half-open receiver must not leave peer carets painted indefinitely.
          presenceExpiry = setTimeout(() => {
            this.participants = {};
            this.notify();
          }, 30000);
          failures = 0;
          if (first) this.send?.();
        } catch (error) {
          this.error =
            error instanceof Error
              ? error.message
              : "Draft synchronization failed.";
          this.notify();
          ws.close(1002, "Invalid draft frame");
        }
      };
      ws.onclose = (event) => {
        if (stopped) return;
        joined = false;
        clearTimeout(presenceExpiry);
        this.status = "Disconnected";
        this.participants = {};
        this.notify();
        if (event.code === 4401) {
          unauthorized();
          return;
        }
        retry = setTimeout(connect, Math.min(1000 * 2 ** failures++, 15000));
      };
    };
    connect();
    return {
      presence: (presence: Schema<"DraftPresence">) => {
        if (
          joined &&
          !this.replacement &&
          this.draftId &&
          socket?.readyState === WebSocket.OPEN
        )
          socket.send(
            JSON.stringify({
              kind: "presence",
              draft_id: this.draftId,
              presence,
            } satisfies Schema<"DraftCommand">),
          );
      },
      close: () => {
        stopped = true;
        this.send = undefined;
        clearTimeout(retry);
        clearTimeout(presenceExpiry);
        socket?.close();
        this.participants = {};
        this.status = "Disconnected";
        this.notify();
      },
    };
  }
}
