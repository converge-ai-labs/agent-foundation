import * as Y from "yjs";
import type { Schema } from "../transport/client";
import { decode, encode, ThreadDraft } from "./draft";

const STORAGE_KEY = "a13n-harness-ui.new-draft";

export type NewDraft = {
  threadId: string;
  defaults: Schema<"NewThreadDefaults">;
  created: boolean;
  attempted: boolean;
  pending?: Promise<void>;
  composer: ThreadDraft;
  save: () => void;
};

function restore(): Omit<NewDraft, "save"> | undefined {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return;
  const saved = JSON.parse(raw);
  if (
    saved?.version !== 1 ||
    typeof saved.threadId !== "string" ||
    !/^thread_[0-9a-f]{32}$/.test(saved.threadId) ||
    typeof saved.update !== "string" ||
    typeof saved.created !== "boolean" ||
    typeof saved.attempted !== "boolean" ||
    !saved.defaults ||
    typeof saved.defaults !== "object"
  )
    return;
  const defaults: Schema<"NewThreadDefaults"> = {};
  for (const key of [
    "project_id",
    "agent_id",
    "environment_profile_id",
  ] as const) {
    const value = saved.defaults[key];
    if (value !== undefined && value !== null && typeof value !== "string")
      return;
    if (value !== undefined) defaults[key] = value;
  }
  const composer = new ThreadDraft();
  try {
    Y.applyUpdate(composer.doc, decode(saved.update));
  } catch (error) {
    composer.undo.destroy();
    composer.doc.destroy();
    throw error;
  }
  if (saved.mode === "goal") composer.mode = "goal";
  if (typeof saved.modelId === "string") composer.modelId = saved.modelId;
  if (
    saved.environment &&
    typeof saved.environment === "object" &&
    !Array.isArray(saved.environment)
  )
    composer.environment = saved.environment;
  if (typeof saved.draftId === "string") composer.draftId = saved.draftId;
  // A reload is not an acknowledgement. Never turn an interrupted Send into
  // an idle composer that can silently replay the same input.
  if (saved.submission === "pending" || saved.submission === "unknown")
    composer.submission = {
      kind: "unknown",
      action: "send",
      message:
        "The previous input may already have been accepted. Open the conversation and review its history before sending again.",
    };
  return {
    threadId: saved.threadId,
    defaults,
    created: saved.created,
    attempted: saved.attempted,
    composer,
  };
}

// One browser-local slot, independent of routes and Project selection. Existing
// Thread composers remain in their ordinary in-tab registry after handoff.
export class NewDraftStore {
  current: NewDraft | undefined;
  error = "";
  private unsubscribe: (() => void) | undefined;
  private restored = false;

  get(composers: Map<string, ThreadDraft>, legacyId?: string): NewDraft {
    if (!this.current) {
      let retained: Omit<NewDraft, "save"> | undefined;
      if (!this.restored) {
        this.restored = true;
        try {
          retained = restore();
        } catch {
          // Malformed or unavailable storage must not prevent local composition.
        }
      }
      this.retain(
        retained ?? {
          threadId:
            legacyId && /^thread_[0-9a-f]{32}$/.test(legacyId)
              ? legacyId
              : `thread_${crypto.randomUUID().replaceAll("-", "")}`,
          defaults: { project_id: null },
          created: false,
          attempted: false,
          composer: new ThreadDraft(),
        },
      );
    }
    const draft = this.current!;
    composers.set(draft.threadId, draft.composer);
    return draft;
  }

  private retain(value: Omit<NewDraft, "save">) {
    this.unsubscribe?.();
    const draft: NewDraft = { ...value, save: () => this.save(draft) };
    this.current = draft;
    this.unsubscribe = draft.composer.subscribe(draft.save);
    return draft;
  }

  detachArchived(threadId: string) {
    const old = this.current;
    if (!old || old.threadId !== threadId) return;
    const composer = new ThreadDraft();
    Y.applyUpdate(composer.doc, Y.encodeStateAsUpdate(old.composer.doc));
    composer.mode = old.composer.mode;
    composer.modelId = old.composer.modelId;
    composer.environment = old.composer.environment
      ? structuredClone(old.composer.environment)
      : undefined;
    // A new Thread cannot use the archived Thread's uploaded handles. Keep
    // visible markers for reattachment, and stage any still-local file bytes.
    for (const key of composer.doc.getMap<string>("attachments").keys()) {
      composer.doc.getMap("attachments").set(key, "failed");
      const upload = old.composer.uploads.get(key);
      if (upload)
        composer.uploads.set(key, { file: upload.file, status: "staged" });
    }
    if (
      old.composer.submission.kind === "unknown" ||
      old.composer.submission.kind === "pending"
    )
      composer.submission = {
        kind: "unknown",
        action: "send",
        message:
          "Review the archived conversation's outcome before sending this input again.",
      };
    this.retain({
      threadId: `thread_${crypto.randomUUID().replaceAll("-", "")}`,
      defaults: { ...old.defaults },
      created: false,
      attempted: false,
      composer,
    }).save();
  }

  private save(draft: NewDraft) {
    if (this.current !== draft) return;
    const previousError = this.error;
    try {
      if (draft.composer.submission.kind === "accepted") {
        try {
          localStorage.removeItem(STORAGE_KEY);
        } finally {
          this.unsubscribe?.();
          this.unsubscribe = undefined;
          this.current = undefined;
        }
      } else {
        localStorage.setItem(
          STORAGE_KEY,
          JSON.stringify({
            version: 1,
            threadId: draft.threadId,
            defaults: draft.defaults,
            created: draft.created,
            attempted: draft.attempted,
            update: encode(Y.encodeStateAsUpdate(draft.composer.doc)),
            draftId: draft.composer.draftId,
            mode: draft.composer.mode,
            modelId: draft.composer.modelId,
            environment: draft.composer.environment,
            submission: draft.composer.submission.kind,
          }),
        );
      }
      this.error = "";
    } catch {
      this.error =
        "Draft could not be saved in this browser. Keep this tab open to retain your input.";
    }
    if (this.error !== previousError) draft.composer.notify();
  }

  dispose() {
    this.unsubscribe?.();
    this.unsubscribe = undefined;
  }
}
