import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import {
  Paperclip,
  Plus,
  ArrowUp,
  Stop,
  X,
  DotsThree,
} from "@phosphor-icons/react";
import type { EditorView } from "@codemirror/view";
import {
  attachmentSelections,
  attachmentToken,
  isReadyAttachment,
} from "./inline-attachments";
import { ImagePreview } from "./image-preview";
import { AttachmentThumbnail } from "./attachment-thumbnail";
import { useTransport } from "../transport/context";
import {
  ApiError,
  result,
  type Schema,
  type Transport,
} from "../transport/client";
import type { Profile } from "../shell/presence";
import { ConfirmAction } from "../shell/confirm-action";
import { ThreadDraft, values, type DraftCapture } from "./draft";
import { ComposerEditor } from "./composer-editor";
import { skillReferences, type LoadSkills } from "./skill-references";
import styles from "./conversation.module.css";
import { useResults } from "./results";
import { commentReference, CommentReferenceContent } from "./comment-reference";
import { previewInput, type LocalInput } from "./local-input";
import type { OrderedInputPart } from "./inline-attachments";

function beginInput(
  draft: ThreadDraft,
  action: "send" | "steer",
  attachments: Map<string, Schema<"ThreadAttachment">>,
  preset?: string,
): LocalInput {
  const text = preset ?? draft.doc.getText("text").toString();
  const parts: OrderedInputPart[] = [];
  let offset = 0;
  for (const selection of preset === undefined
    ? attachmentSelections(draft.doc)
    : []) {
    const start = selection.from ?? text.length;
    if (start > offset) parts.push(text.slice(offset, start));
    offset = selection.to ?? text.length;
    parts.push(
      isReadyAttachment(selection.id)
        ? { attachment_id: selection.id }
        : `[${draft.uploads.get(selection.key)?.file.name ?? "Attachment"}]`,
    );
  }
  if (offset < text.length) parts.push(text.slice(offset));
  const id = `input_${crypto.randomUUID().replaceAll("-", "")}`;
  const input: LocalInput = {
    id,
    action,
    parts: previewInput(id, parts, attachments),
    state: "preparing",
  };
  draft.localInputs = [
    ...draft.localInputs.filter((item) => item.state !== "rejected"),
    input,
  ];
  draft.notify();
  return input;
}

export const ComposerDrafts = createContext(new Map<string, ThreadDraft>());
export function useDraft(threadId: string) {
  const drafts = useContext(ComposerDrafts);
  let draft = drafts.get(threadId);
  if (!draft) {
    draft = new ThreadDraft();
    drafts.set(threadId, draft);
  }
  useSyncExternalStore(draft.subscribe, draft.getSnapshot);
  return draft;
}

export function waitForSynchronization(
  draft: ThreadDraft,
  signal: AbortSignal,
) {
  signal.throwIfAborted();
  if (draft.synchronized) return Promise.resolve();
  return new Promise<void>((resolve, reject) => {
    const finish = (error?: Error) => {
      clearTimeout(timer);
      unsubscribe();
      signal.removeEventListener("abort", abort);
      if (error) reject(error);
      else resolve();
    };
    const abort = () =>
      finish(new Error("Preparation cancelled. Your input is retained."));
    const unsubscribe = draft.subscribe(() => {
      if (draft.synchronized) finish();
      else if (draft.error || draft.replacement)
        finish(
          new Error(
            draft.error ||
              "The shared draft changed. Review your input before sending.",
          ),
        );
    });
    const timer = setTimeout(
      () =>
        finish(
          new Error(
            "Could not synchronize yet. Your input is retained; send again when connected.",
          ),
        ),
      15000,
    );
    signal.addEventListener("abort", abort, { once: true });
  });
}

export async function submitDraft(
  draft: ThreadDraft,
  transport: Transport,
  threadId: string,
  action: "send" | "steer",
  receipt?: string,
  modelId?: string,
  localInput?: LocalInput,
  attachments = new Map<string, Schema<"ThreadAttachment">>(),
  loadSkills?: LoadSkills,
  signal?: AbortSignal,
  preset?: string,
  prepare?: () => Promise<void>,
) {
  if (
    draft.submission.kind === "pending" ||
    draft.submission.kind === "unknown"
  )
    return;
  // Own the shared submission state before any asynchronous preparation so
  // Retry and ordinary Send/Steer cannot race while synchronization or skills load.
  draft.submission = { kind: "pending", action };
  const input = localInput ?? beginInput(draft, action, attachments, preset);
  draft.notify();
  let captured: DraftCapture | undefined;
  let parts: OrderedInputPart[];
  let references: Schema<"SkillReference">[] = [];
  try {
    if (prepare) await prepare();
    signal?.throwIfAborted();
    if (preset === undefined) {
      captured = draft.capture();
      parts = captured.parts;
      if (loadSkills && /(?:^|\s)\$\S+/.test(captured.input.prompt))
        references = skillReferences(parts, await loadSkills());
    } else parts = [preset];
    signal?.throwIfAborted();
  } catch (error) {
    input.state = "rejected";
    draft.submission = {
      kind: "rejected",
      message: error instanceof Error ? error.message : "Cannot capture input.",
    };
    captured?.doc.destroy();
    draft.notify();
    return;
  }
  input.parts = previewInput(input.id, parts, attachments);
  input.state = "pending";
  draft.submission = { kind: "pending", action };
  draft.notify();
  try {
    let acceptedReceipt: string;
    if (action === "send") {
      const accepted = await result(
        transport.client.POST("/api/threads/{thread_id}/submit", {
          params: { path: { thread_id: threadId } },
          body: {
            parts,
            source_id: input.id,
            ...(references.length ? { skill_references: references } : {}),
            ...(modelId ? { model_id: modelId } : {}),
          },
        }),
      );
      if (!accepted.receipt_id || accepted.thread_id !== threadId)
        throw new Error("Submission acknowledgement was incomplete.");
      acceptedReceipt = accepted.receipt_id;
    } else {
      if (!receipt)
        throw new ApiError(
          "The current operation is unavailable. Refresh its status.",
          409,
        );
      const accepted = await result(
        transport.client.POST("/api/operations/{receipt_id}/steer", {
          params: { path: { receipt_id: receipt } },
          body: {
            parts,
            source_id: input.id,
            ...(references.length ? { skill_references: references } : {}),
          },
        }),
      );
      if (accepted.receipt_id !== receipt)
        throw new Error(
          "Instruction acknowledgement did not match this operation.",
        );
      if (!accepted.accepted)
        throw new ApiError(
          "This operation did not accept the instruction. Your input is retained.",
          409,
        );
      acceptedReceipt = receipt;
    }
    input.state = "accepted";
    if (captured) draft.clear(captured);
    draft.submission = {
      kind: "accepted",
      action,
      receipt: acceptedReceipt,
    };
  } catch (error) {
    // A definite application rejection differs from a lost response/proxy failure.
    if (
      error instanceof ApiError &&
      error.status >= 400 &&
      error.status < 500
    ) {
      input.state = "rejected";
      draft.submission = { kind: "rejected", message: error.message };
    } else {
      input.state = "unknown";
      draft.submission = {
        kind: "unknown",
        action,
        receipt,
        message:
          "The acknowledgement was lost. Your input is retained; it may already have been accepted. Inspect the operation and history before deciding what to send next.",
      };
    }
  } finally {
    captured?.doc.destroy();
    draft.notify();
  }
  return true;
}

export function submitContinuation(
  draft: ThreadDraft,
  transport: Transport,
  threadId: string,
  prepare?: () => Promise<void>,
) {
  return submitDraft(
    draft,
    transport,
    threadId,
    "send",
    undefined,
    draft.modelId,
    undefined,
    undefined,
    undefined,
    undefined,
    "Continue completing the previous task.",
    prepare,
  );
}

export function Composer({
  referenceAdded = 0,
  autoFocus = false,
  local = false,
  skillDefaults,
  prepareThread,
  onPreparing,
  onSubmitted,
  controls,
  leadingControls,
  modelId,
  threadId,
  activity,
  canRun,
  profile,
  unauthorized,
  reconcile,
}: {
  threadId: string;
  activity: Schema<"RootActivityView">;
  canRun: boolean;
  profile: Profile;
  unauthorized: () => void;
  reconcile: () => void;
  referenceAdded?: number;
  autoFocus?: boolean;
  local?: boolean;
  skillDefaults?: Schema<"NewThreadDefaults">;
  prepareThread?: () => Promise<void>;
  onPreparing?: (preparing: boolean) => void;
  onSubmitted?: () => void | Promise<void>;
  controls?: ReactNode;
  leadingControls?: ReactNode;
  modelId?: string;
}) {
  const draft = useDraft(threadId);
  const { tracker: results } = useResults();
  const [preparing, setPreparing] = useState(false);
  const preparation = useRef<AbortController | null>(null);
  useEffect(() => () => preparation.current?.abort(), [threadId]);
  const transport = useTransport();
  const queries = useQueryClient();
  const connection = useRef<ReturnType<ThreadDraft["connect"]> | null>(null);
  const upload = useRef<HTMLInputElement>(null);
  const editor = useRef<EditorView | null>(null);
  const [mobileOptions, setMobileOptions] = useState(false);
  useEffect(() => {
    if (!referenceAdded) return;
    const frame = requestAnimationFrame(() => {
      const view = editor.current;
      if (!view) return;
      view.dispatch({
        selection: { anchor: view.state.doc.length },
        scrollIntoView: true,
      });
      view.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, [referenceAdded]);
  const previewRequest = useRef<AbortController | null>(null);
  useEffect(() => () => previewRequest.current?.abort(), [transport, threadId]);
  const [error, setError] = useState("");
  const skillContext = JSON.stringify([
    local,
    skillDefaults,
    activity.receipt_id,
  ]);
  const loadSkills: LoadSkills = () =>
    queries.fetchQuery({
      queryKey: ["thread", threadId, "skills", skillContext],
      queryFn: ({ signal }) =>
        local
          ? result(
              transport.client.POST("/api/threads/skills-preview", {
                body: skillDefaults ?? {},
                signal,
              }),
            )
          : result(
              transport.client.GET("/api/threads/{thread_id}/skills", {
                params: { path: { thread_id: threadId } },
                signal,
              }),
            ),
      staleTime: 10000,
    });
  const [syncDelayed, setSyncDelayed] = useState(false);
  const synchronized = draft.synchronized;
  useEffect(() => {
    setSyncDelayed(false);
    if (synchronized || local) return;
    const timer = setTimeout(() => setSyncDelayed(true), 700);
    return () => clearTimeout(timer);
  }, [synchronized, draft.status, local]);
  const [preview, setPreview] = useState<{
    name: string;
    text: string;
    url?: string;
    source?: Schema<"ThreadAttachment">["source"];
    comment?: Schema<"ThreadAttachment">["comment"];
    image?: boolean;
  } | null>(null);
  const selections = attachmentSelections(draft.doc);
  const uploading = selections.some(
    ({ key, id }) =>
      id === "pending" && draft.uploads.get(key)?.status !== "staged",
  );
  const attachments = useQueries({
    queries: selections.map(({ id }) => ({
      enabled: !local && isReadyAttachment(id),
      queryKey: ["thread", threadId, "attachment", id],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        result(
          transport.client.GET(
            "/api/threads/{thread_id}/attachments/{attachment_id}/metadata",
            {
              params: { path: { thread_id: threadId, attachment_id: id! } },
              signal,
            },
          ),
        ),
      staleTime: Infinity,
    })),
  });
  useEffect(() => {
    if (local) return;
    const current = draft.connect(transport, threadId, unauthorized);
    connection.current = current;
    return () => {
      current.close();
      connection.current = null;
    };
  }, [draft, transport, threadId, unauthorized, local]);
  useEffect(
    () => () => {
      if (preview?.url) URL.revokeObjectURL(preview.url);
    },
    [preview],
  );
  const busy = activity.state !== "inactive";
  const pending = draft.submission.kind === "pending";
  const unknown = draft.submission.kind === "unknown";
  const input = values(draft.doc);
  const missing = selections.some(({ key, id }, index) =>
    prepareThread && draft.uploads.get(key)?.status === "staged"
      ? false
      : !isReadyAttachment(id) || !attachments[index].data,
  );
  const hasInput = !!(input.prompt.trim() || selections.length);
  const valid = hasInput && !missing;
  const [stopping, setStopping] = useState(false);
  const ready =
    local ||
    (draft.status === "Connected" && !draft.replacement && !draft.error);
  const canSteer =
    busy &&
    ready &&
    !preparing &&
    !pending &&
    !unknown &&
    valid &&
    !!activity.available_actions?.includes("steer");
  const stopAction = busy && !hasInput;
  const canStop =
    stopAction &&
    !!activity.receipt_id &&
    !!activity.available_actions?.includes("cancel") &&
    !stopping &&
    !preparing &&
    !pending;
  const stop = async () => {
    if (!canStop || !activity.receipt_id) return;
    setStopping(true);
    setError("");
    try {
      await result(
        transport.client.POST("/api/operations/{receipt_id}/cancel", {
          params: { path: { receipt_id: activity.receipt_id } },
        }),
      );
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "Could not confirm Stop. Refresh the operation before retrying.",
      );
    } finally {
      setStopping(false);
      reconcile();
    }
  };
  const canSend =
    canRun && !busy && ready && !preparing && !pending && !unknown && valid;
  const submit = async (action: "send" | "steer") => {
    if (action === "send" && !canSend) return;
    if (
      action === "steer" &&
      (!ready ||
        pending ||
        unknown ||
        !valid ||
        !activity.available_actions?.includes("steer"))
    )
      return;
    if (
      preparation.current ||
      draft.submission.kind === "pending" ||
      draft.submission.kind === "unknown"
    )
      return;
    const controller = new AbortController();
    preparation.current = controller;
    const attachmentMetadata = () =>
      new Map(
        attachmentSelections(draft.doc).flatMap(({ id }) => {
          const item = queries.getQueryData<Schema<"ThreadAttachment">>([
            "thread",
            threadId,
            "attachment",
            id,
          ]);
          return item && id ? [[id, item] as const] : [];
        }),
      );
    const metadata = attachmentMetadata();
    const localInput = beginInput(draft, action, metadata);
    try {
      setPreparing(true);
      onPreparing?.(true);
      setError("");
      const submitted = await submitDraft(
        draft,
        transport,
        threadId,
        action,
        activity.receipt_id ?? undefined,
        modelId,
        localInput,
        metadata,
        loadSkills,
        controller.signal,
        undefined,
        prepareThread || results || !draft.synchronized
          ? async () => {
              if (prepareThread) {
                await prepareThread();
                controller.signal.throwIfAborted();
                await Promise.all(
                  attachmentSelections(draft.doc).flatMap(({ key }) => {
                    const item = draft.uploads.get(key);
                    return item?.status === "staged"
                      ? [uploadOne(key, item.file)]
                      : [];
                  }),
                );
              }
              if (results) await results.beforeRun(threadId);
              // Explicit Send/Steer waits for the complete shared snapshot while
              // retaining ownership against other submission entry points.
              if (!draft.synchronized)
                await waitForSynchronization(draft, controller.signal);
              controller.signal.throwIfAborted();
              for (const [id, attachment] of attachmentMetadata())
                metadata.set(id, attachment);
            }
          : undefined,
      );
      reconcile();
      if (submitted && !controller.signal.aborted) await onSubmitted?.();
    } catch (failure) {
      // A failed follow-up observation cannot undo an admission receipt.
      if (localInput.state === "preparing") {
        localInput.state = "rejected";
        draft.notify();
      }
      if (!controller.signal.aborted)
        setError(
          failure instanceof Error
            ? failure.message
            : "Could not prepare the conversation. Your input is retained.",
        );
    } finally {
      preparation.current = null;
      setPreparing(false);
      onPreparing?.(false);
    }
  };
  async function uploadOne(key: string, file: File) {
    setError("");
    const doc = draft.doc;
    const incarnation = draft.draftId;
    draft.uploads.set(key, { file, status: "pending" });
    doc.getMap("attachments").set(key, "pending");
    try {
      const response = await transport.fetch(
        `/api/threads/${encodeURIComponent(threadId)}/attachments?name=${encodeURIComponent(file.name)}`,
        {
          method: "POST",
          headers: { "Content-Type": file.type || "application/octet-stream" },
          body: file,
        },
      );
      const attachment = (await response.json()) as Schema<"ThreadAttachment">;
      queries.setQueryData(
        ["thread", threadId, "attachment", attachment.attachment_id],
        attachment,
      );
      if (
        draft.doc !== doc ||
        (incarnation !== undefined && draft.draftId !== incarnation) ||
        draft.replacement
      )
        return;
      // Keep the registry even if the token was deleted: native undo can restore it.
      doc.getMap("attachments").set(key, attachment.attachment_id);
      draft.uploads.delete(key);
    } catch (failure) {
      if (
        draft.doc !== doc ||
        (incarnation !== undefined && draft.draftId !== incarnation)
      )
        return;
      draft.uploads.set(key, { file, status: "failed" });
      doc.getMap("attachments").set(key, "failed");
      setError(
        failure instanceof Error
          ? failure.message
          : "Upload failed. Retry or remove the attachment.",
      );
    }
  }
  async function uploadFiles(
    files: File[],
    at = editor.current?.state.selection.main.head ??
      draft.doc.getText("text").length,
  ) {
    if (!files.length || preparation.current) return;
    setError("");
    try {
      if (attachmentSelections(draft.doc).length + files.length > 8)
        throw new Error("Select up to eight attachments.");
      const total =
        attachmentSelections(draft.doc).reduce((size, { key, id }) => {
          const retained = queries.getQueryData<Schema<"ThreadAttachment">>([
            "thread",
            threadId,
            "attachment",
            id,
          ]);
          return (
            size + (retained?.size ?? draft.uploads.get(key)?.file.size ?? 0)
          );
        }, 0) + files.reduce((size, file) => size + file.size, 0);
      if (total > 20 * 1024 * 1024)
        throw new Error("Selected attachments exceed 20 MiB.");
      for (const file of files)
        if (file.size > 10 * 1024 * 1024)
          throw new Error(`${file.name} exceeds 10 MiB.`);
      // Reserve every position synchronously, before any upload can finish.
      const pending = files.map((file) => {
        const key = draft.addAttachment("pending", at);
        draft.uploads.set(key, { file, status: local ? "staged" : "pending" });
        at += attachmentToken(key).length;
        return { key, file };
      });
      editor.current?.dispatch({
        selection: { anchor: at },
        scrollIntoView: true,
      });
      editor.current?.focus();
      if (!local)
        await Promise.all(pending.map(({ key, file }) => uploadOne(key, file)));
      else draft.notify();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Upload failed.");
    } finally {
      if (upload.current) upload.current.value = "";
    }
  }
  async function showAttachment(
    id: string,
    attachment?: Schema<"ThreadAttachment">,
  ) {
    setError("");
    previewRequest.current?.abort();
    const controller = new AbortController();
    previewRequest.current = controller;
    try {
      const response = await transport.fetch(
        `/api/threads/${encodeURIComponent(threadId)}/attachments/${encodeURIComponent(id)}`,
        { signal: controller.signal },
      );
      const blob = await response.blob();
      let text = "Binary attachment. Download to inspect its original bytes.";
      if (blob.size <= 256 * 1024) {
        try {
          const decoded = new TextDecoder("utf-8", { fatal: true }).decode(
            await blob.arrayBuffer(),
          );
          if (!decoded.includes("\0")) text = decoded;
        } catch {
          /* Binary content is never injected into HTML. */
        }
      } else
        text =
          "This attachment is too large for inline preview. Download to inspect it.";
      if (controller.signal.aborted) return;
      setPreview({
        name: attachment?.name ?? id,
        text,
        url: URL.createObjectURL(blob),
        source: attachment?.source,
        comment: attachment?.comment,
        image: attachment?.media_type.startsWith("image/"),
      });
    } catch (failure) {
      if (controller.signal.aborted) return;
      setError(
        failure instanceof Error ? failure.message : "Preview unavailable.",
      );
    }
  }
  const comment = commentReference(preview);
  return (
    <section
      className={styles.composer}
      aria-label={busy ? "Next message" : "Message composer"}
    >
      {attachments.some((attachment) => commentReference(attachment.data)) && (
        <p role="status" className={styles.composerConnection}>
          Comment added to your message. Review it below, then send when ready.
        </p>
      )}
      {!local && !synchronized && !draft.replacement && syncDelayed && (
        <p role="status" className={styles.composerConnection}>
          {draft.status === "Connected"
            ? "Syncing edits…"
            : `${draft.status} · your edits are still in this tab`}
        </p>
      )}
      {draft.replacement && (
        <div role="alert" className={styles.warning}>
          <p>
            The server's shared draft changed. Your previous text is still here.
            Rejoining never submits it.
          </p>
          <Button variant="outline" onClick={() => draft.joinReplacement(true)}>
            Add local input to the new draft
          </Button>
          <ConfirmAction
            key={draft.replacement.draft_id}
            trigger={
              <Button variant="ghost">Discard old input and rejoin</Button>
            }
            title="Discard old input?"
            description="Discard this tab's old input and join the new shared draft?"
            confirmLabel="Discard and rejoin"
            destructive
            onConfirm={() => draft.joinReplacement(false)}
          />
        </div>
      )}
      <div className={styles.composerBody}>
        <div inert={preparing} className={styles.composerEditor}>
          <ComposerEditor
            autoFocus={autoFocus}
            local={local}
            skillContext={skillContext}
            loadSkills={async () => {
              try {
                return await loadSkills();
              } catch (failure) {
                setError(
                  failure instanceof Error
                    ? `Could not load skills: ${failure.message}`
                    : "Could not load skills. Type $ again to retry.",
                );
                throw failure;
              }
            }}
            draft={draft}
            profile={profile}
            presence={(value) => connection.current?.presence(value)}
            submit={() => void submit(busy ? "steer" : "send")}
            editor={editor}
            attachments={{
              transport,
              threadId,
              metadata: new Map(
                attachments.flatMap((item) =>
                  item.data
                    ? [[item.data.attachment_id, item.data] as const]
                    : [],
                ),
              ),
              preview: (id, attachment) => void showAttachment(id, attachment),
              upload: (files, at) => void uploadFiles(files, at),
              retry: (key) => {
                const item = draft.uploads.get(key);
                if (
                  !local &&
                  (item?.status === "failed" || item?.status === "staged")
                )
                  void uploadOne(key, item.file);
                else if (!item)
                  setError(
                    "This upload is unavailable in this tab. Remove it and attach the file again.",
                  );
              },
            }}
          />
        </div>
        {selections.some((selection) => selection.from === undefined) && (
          <ul className={styles.attachments}>
            {selections.map(({ key, id, from }, index) =>
              from !== undefined ? null : (
                <li key={key}>
                  <button
                    type="button"
                    onClick={() =>
                      id && void showAttachment(id, attachments[index].data)
                    }
                  >
                    {attachments[index].data && (
                      <AttachmentThumbnail
                        threadId={threadId}
                        attachment={attachments[index].data}
                      />
                    )}
                    <Paperclip /> {attachments[index].data?.name ?? id}
                    {attachments[index].data?.source && (
                      <small>
                        Captured{" "}
                        {"comment_id" in attachments[index].data.source
                          ? "comment reference"
                          : "repository_path" in attachments[index].data.source
                            ? "diff"
                            : "file"}
                      </small>
                    )}
                  </button>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Remove ${attachments[index].data?.name ?? id}`}
                    onClick={() => draft.removeAttachment(key)}
                  >
                    <X />
                  </Button>
                  {attachments[index].error && (
                    <span role="alert">
                      Attachment unavailable; remove it or refresh access.
                    </span>
                  )}
                </li>
              ),
            )}
          </ul>
        )}
        {(error || draft.error) && (
          <p role="alert" className={styles.warning}>
            {error || draft.error}
          </p>
        )}
        {(draft.submission.kind === "rejected" || unknown) && (
          <div role="alert" className={styles.warning}>
            <p>{"message" in draft.submission && draft.submission.message}</p>
            {unknown &&
              "receipt" in draft.submission &&
              draft.submission.receipt && (
                <details>
                  <summary>Submission details</summary>
                  <small>Operation: {draft.submission.receipt}</small>
                </details>
              )}
            {unknown && (
              <>
                <Button variant="outline" onClick={reconcile}>
                  Refresh operation and history
                </Button>
                <ConfirmAction
                  trigger={
                    <Button variant="ghost">I reviewed the outcome</Button>
                  }
                  title="Enable a new submission?"
                  description="The previous input may already have been accepted. Enable a deliberate new submission only after reviewing the conversation."
                  confirmLabel="Enable submission"
                  onConfirm={() => {
                    draft.submission = { kind: "idle" };
                    draft.notify();
                  }}
                />
              </>
            )}
          </div>
        )}
        <div className={styles.composerActions}>
          <div>
            <input
              ref={upload}
              type="file"
              multiple
              hidden
              onChange={(event) =>
                void uploadFiles(Array.from(event.target.files ?? []))
              }
            />
            <Button
              variant="ghost"
              size="icon-sm"
              className={styles.attachButton}
              aria-label="Attach files"
              title="Attach files · you can also paste or drop files"
              loading={uploading}
              disabled={preparing}
              onClick={() => upload.current?.click()}
            >
              <Plus />
            </Button>
            <div className={styles.composerOptions} data-open={mobileOptions}>
              {leadingControls}
            </div>
          </div>
          <div>
            <Button
              variant="ghost"
              size="icon-sm"
              className={styles.mobileOptionsButton}
              aria-label="Composer options"
              aria-expanded={mobileOptions}
              onClick={() => setMobileOptions(!mobileOptions)}
            >
              <DotsThree />
            </Button>
            <div className={styles.composerOptions} data-open={mobileOptions}>
              {controls}
            </div>
            <Button
              size="icon"
              className={styles.sendButton}
              aria-label={
                pending
                  ? "Submitting"
                  : preparing
                    ? "Preparing"
                    : stopAction
                      ? "Stop"
                      : busy
                        ? "Steer"
                        : "Send"
              }
              title={
                stopAction
                  ? "Stop this operation"
                  : busy
                    ? "Steer current operation · Enter"
                    : "Send message · Enter"
              }
              disabled={stopAction ? !canStop : busy ? !canSteer : !canSend}
              loading={pending || preparing || stopping}
              onClick={() =>
                stopAction ? void stop() : void submit(busy ? "steer" : "send")
              }
            >
              {stopAction ? <Stop weight="fill" /> : <ArrowUp />}
            </Button>
          </div>
        </div>
      </div>
      {preview?.image && preview.url && (
        <ImagePreview
          src={preview.url}
          name={preview.name}
          close={() => setPreview(null)}
        />
      )}
      <ModalFrame
        open={!!preview && !preview.image}
        onOpenChange={(open) => {
          if (!open) {
            previewRequest.current?.abort();
            setPreview(null);
          }
        }}
        title={comment ? "Comment reference" : (preview?.name ?? "Attachment")}
        description={
          comment
            ? "Review the comment and response included in your message."
            : "These are the retained bytes selected for input, not the current file on the server."
        }
        closeLabel="Close"
      >
        {preview?.source && !comment && (
          <div className={styles.summary}>
            {"path" in preview.source && (
              <p>Server native capture · {preview.source.path}</p>
            )}
            {"repository_path" in preview.source && (
              <p>
                {preview.source.comparison} · {preview.source.repository_path} ·
                HEAD {preview.source.head_oid ?? "empty tree"}
              </p>
            )}
            <details>
              <summary>Captured source identity</summary>
              <pre className={styles.code}>
                {JSON.stringify(preview.source, null, 2)}
              </pre>
            </details>
            <p>
              These are immutable captured bytes, not a fresh read of the
              source. Sending retains this content with its source metadata.
            </p>
          </div>
        )}
        {comment && preview ? (
          <CommentReferenceContent source={comment} text={preview.text} />
        ) : (
          <pre className={styles.code}>{preview?.text}</pre>
        )}
        {preview?.url && (
          <a href={preview.url} download={preview.name}>
            Download original
          </a>
        )}
      </ModalFrame>
    </section>
  );
}
