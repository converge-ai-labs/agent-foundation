import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  ModalFrame,
  Popover,
  PopoverTrigger,
  PopoverPopup,
  PopoverTitle,
} from "a13n-ui";
import { Paperclip, ArrowUp, Question, X } from "@phosphor-icons/react";
import type { EditorView } from "@codemirror/view";
import {
  attachmentSelections,
  attachmentToken,
  isReadyAttachment,
} from "./inline-attachments";
import { AttachmentThumbnail } from "./attachment-thumbnail";
import { useTransport } from "../transport/context";
import {
  ApiError,
  result,
  type Schema,
  type Transport,
} from "../transport/client";
import type { Profile } from "../shell/presence";
import { ThreadDraft, values, type DraftCapture } from "./draft";
import { ComposerEditor } from "./composer-editor";
import styles from "./conversation.module.css";

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

export async function submitDraft(
  draft: ThreadDraft,
  transport: Transport,
  threadId: string,
  action: "send" | "steer",
  receipt?: string,
) {
  if (
    draft.submission.kind === "pending" ||
    draft.submission.kind === "unknown"
  )
    return;
  let captured: DraftCapture;
  try {
    captured = draft.capture();
  } catch (error) {
    draft.submission = {
      kind: "rejected",
      message: error instanceof Error ? error.message : "Cannot capture input.",
    };
    draft.notify();
    return;
  }
  draft.submission = { kind: "pending", action };
  draft.notify();
  try {
    let acceptedReceipt: string;
    if (action === "send") {
      const accepted = await result(
        transport.client.POST("/api/threads/{thread_id}/submit", {
          params: { path: { thread_id: threadId } },
          body: { parts: captured.parts },
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
          body: { parts: captured.parts },
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
    draft.clear(captured);
    draft.submission = {
      kind: "accepted",
      receipt: acceptedReceipt,
      message:
        action === "send"
          ? "Input accepted. Execution may still be preparing."
          : "Instruction accepted by the current operation.",
    };
  } catch (error) {
    // A definite application rejection differs from a lost response/proxy failure.
    if (
      error instanceof ApiError &&
      error.status >= 400 &&
      error.status < 500
    ) {
      draft.submission = { kind: "rejected", message: error.message };
    } else {
      draft.submission = {
        kind: "unknown",
        action,
        receipt,
        message:
          "The acknowledgement was lost. Your input is retained; it may already have been accepted. Inspect the operation and history before deciding what to send next.",
      };
    }
  } finally {
    captured.doc.destroy();
    draft.notify();
  }
}

export function Composer({
  autoFocus = false,
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
  autoFocus?: boolean;
}) {
  const draft = useDraft(threadId);
  const transport = useTransport();
  const queries = useQueryClient();
  const connection = useRef<ReturnType<ThreadDraft["connect"]> | null>(null);
  const upload = useRef<HTMLInputElement>(null);
  const editor = useRef<EditorView | null>(null);
  const previewRequest = useRef<AbortController | null>(null);
  useEffect(() => () => previewRequest.current?.abort(), [transport, threadId]);
  const [error, setError] = useState("");
  const [syncDelayed, setSyncDelayed] = useState(false);
  const synchronized = draft.synchronized;
  useEffect(() => {
    setSyncDelayed(false);
    if (synchronized || draft.status !== "Connected") return;
    const timer = setTimeout(() => setSyncDelayed(true), 700);
    return () => clearTimeout(timer);
  }, [synchronized, draft.status]);
  const [preview, setPreview] = useState<{
    name: string;
    text: string;
    url?: string;
    source?: Schema<"ThreadAttachment">["source"];
    image?: boolean;
  } | null>(null);
  const selections = attachmentSelections(draft.doc);
  const uploading = selections.some(({ id }) => id === "pending");
  const attachments = useQueries({
    queries: selections.map(({ id }) => ({
      enabled: isReadyAttachment(id),
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
    const current = draft.connect(transport, threadId, unauthorized);
    connection.current = current;
    return () => {
      current.close();
      connection.current = null;
    };
  }, [draft, transport, threadId, unauthorized]);
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
  const missing = attachments.some((attachment) => !attachment.data);
  const valid =
    !!(input.prompt.trim() || input.attachment_ids.length) && !missing;
  const canSend =
    canRun && !busy && draft.synchronized && !pending && !unknown && valid;
  const submit = async (action: "send" | "steer") => {
    if (action === "send" && !canSend) return;
    if (
      action === "steer" &&
      (!draft.synchronized ||
        pending ||
        unknown ||
        !valid ||
        !activity.available_actions?.includes("steer"))
    )
      return;
    await submitDraft(
      draft,
      transport,
      threadId,
      action,
      activity.receipt_id ?? undefined,
    );
    reconcile();
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
    if (!files.length) return;
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
        draft.uploads.set(key, { file, status: "pending" });
        at += attachmentToken(key).length;
        return { key, file };
      });
      editor.current?.dispatch({
        selection: { anchor: at },
        scrollIntoView: true,
      });
      editor.current?.focus();
      await Promise.all(pending.map(({ key, file }) => uploadOne(key, file)));
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
        image: attachment?.media_type.startsWith("image/"),
      });
    } catch (failure) {
      if (controller.signal.aborted) return;
      setError(
        failure instanceof Error ? failure.message : "Preview unavailable.",
      );
    }
  }
  return (
    <section
      className={styles.composer}
      aria-label={busy ? "Next message" : "Message composer"}
    >
      {!synchronized &&
        !draft.replacement &&
        (draft.status !== "Connected" || syncDelayed) && (
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
          <Button
            variant="ghost"
            onClick={() => {
              if (
                window.confirm(
                  "Discard this tab's old input and join the new shared draft?",
                )
              )
                draft.joinReplacement(false);
            }}
          >
            Discard old input and rejoin
          </Button>
        </div>
      )}
      <ComposerEditor
        autoFocus={autoFocus}
        draft={draft}
        profile={profile}
        presence={(value) => connection.current?.presence(value)}
        submit={() => void submit("send")}
        editor={editor}
        attachments={{
          transport,
          threadId,
          metadata: new Map(
            attachments.flatMap((item) =>
              item.data ? [[item.data.attachment_id, item.data] as const] : [],
            ),
          ),
          preview: (id, attachment) => void showAttachment(id, attachment),
          upload: (files, at) => void uploadFiles(files, at),
          retry: (key) => {
            const item = draft.uploads.get(key);
            if (item?.status === "failed") void uploadOne(key, item.file);
            else if (!item)
              setError(
                "This upload is unavailable in this tab. Remove it and attach the file again.",
              );
          },
        }}
      />
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
              <Button
                variant="ghost"
                onClick={() => {
                  if (
                    window.confirm(
                      "The previous input may already have been accepted. Enable a deliberate new submission only after reviewing the conversation?",
                    )
                  ) {
                    draft.submission = { kind: "idle" };
                    draft.notify();
                  }
                }}
              >
                I reviewed the outcome
              </Button>
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
            aria-label="Attach files"
            title="Attach files"
            loading={uploading}
            onClick={() => upload.current?.click()}
          >
            <Paperclip />
          </Button>
          <Popover>
            <PopoverTrigger
              render={<Button variant="ghost" size="icon-sm" />}
              aria-label="Composer help"
            >
              <Question />
            </PopoverTrigger>
            <PopoverPopup
              side="top"
              align="start"
              className={styles.composerHelp}
            >
              <PopoverTitle>Writing a message</PopoverTitle>
              <p>Enter for a new line · Ctrl/⌘+Enter to send.</p>
              <p>Paste or drop files to attach them.</p>
              <p>
                Drafts are shared with people in this conversation. They are not
                saved across server restarts.
              </p>
              <p>
                While the agent is working, keep drafting here or use Send as
                instruction. A next message is not queued automatically.
              </p>
            </PopoverPopup>
          </Popover>
          {busy && (
            <span className={styles.composerContext}>Draft next message</span>
          )}
        </div>
        <div>
          {busy && activity.available_actions?.includes("steer") && (
            <Button
              variant="outline"
              disabled={!draft.synchronized || pending || unknown || !valid}
              onClick={() => void submit("steer")}
            >
              Send as instruction
            </Button>
          )}
          <Button
            size="sm"
            title="Send message · Ctrl/⌘+Enter"
            disabled={!canSend}
            loading={pending}
            onClick={() => void submit("send")}
          >
            <ArrowUp />
            {pending ? "Submitting" : "Send"}
          </Button>
        </div>
      </div>
      <ModalFrame
        open={!!preview}
        onOpenChange={(open) => {
          if (!open) {
            previewRequest.current?.abort();
            setPreview(null);
          }
        }}
        title={preview?.name ?? "Attachment"}
        description="These are the retained bytes selected for input, not the current file on the server."
        closeLabel="Close"
      >
        {preview?.source && (
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
        {preview?.image && preview.url ? (
          <img
            className={styles.attachmentPreview}
            src={preview.url}
            alt={preview.name}
          />
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
