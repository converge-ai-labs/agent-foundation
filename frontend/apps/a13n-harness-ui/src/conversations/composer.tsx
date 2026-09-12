import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { Paperclip, ArrowUp, X } from "@phosphor-icons/react";
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
          body: captured.input,
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
          body: captured.input,
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
}) {
  const draft = useDraft(threadId);
  const transport = useTransport();
  const queries = useQueryClient();
  const connection = useRef<ReturnType<ThreadDraft["connect"]> | null>(null);
  const upload = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<{
    name: string;
    text: string;
    url?: string;
  } | null>(null);
  const selections = [...draft.doc.getMap<string>("attachments").entries()];
  const attachments = useQueries({
    queries: selections.map(([, id]) => ({
      queryKey: ["thread", threadId, "attachment", id],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        result(
          transport.client.GET(
            "/api/threads/{thread_id}/attachments/{attachment_id}/metadata",
            {
              params: { path: { thread_id: threadId, attachment_id: id } },
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
  const unsupportedSteer = attachments.some(
    (attachment) =>
      !attachment.data?.source || attachment.data.size > 64 * 1024,
  );
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
        unsupportedSteer ||
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
  async function uploadFiles(files: FileList | null) {
    if (!files) return;
    setError("");
    setUploading(true);
    try {
      if (selections.length + files.length > 8)
        throw new Error("Select up to eight attachments.");
      const total =
        attachments.reduce((size, item) => size + (item.data?.size ?? 0), 0) +
        Array.from(files).reduce((size, file) => size + file.size, 0);
      if (total > 20 * 1024 * 1024)
        throw new Error("Selected attachments exceed 20 MiB.");
      for (const file of files) {
        if (file.size > 10 * 1024 * 1024)
          throw new Error(`${file.name} exceeds 10 MiB.`);
        const response = await transport.fetch(
          `/api/threads/${encodeURIComponent(threadId)}/attachments?name=${encodeURIComponent(file.name)}`,
          {
            method: "POST",
            headers: {
              "Content-Type": file.type || "application/octet-stream",
            },
            body: file,
          },
        );
        const attachment =
          (await response.json()) as Schema<"ThreadAttachment">;
        queries.setQueryData(
          ["thread", threadId, "attachment", attachment.attachment_id],
          attachment,
        );
        draft.doc
          .getMap("attachments")
          .set(crypto.randomUUID(), attachment.attachment_id);
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Upload failed.");
    } finally {
      setUploading(false);
      if (upload.current) upload.current.value = "";
    }
  }
  async function showAttachment(
    id: string,
    attachment?: Schema<"ThreadAttachment">,
  ) {
    setError("");
    try {
      const response = await transport.fetch(
        `/api/threads/${encodeURIComponent(threadId)}/attachments/${encodeURIComponent(id)}`,
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
      setPreview({
        name: attachment?.name ?? id,
        text,
        url: URL.createObjectURL(blob),
      });
    } catch (failure) {
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
      <div className={styles.composerHeading}>
        <strong>{busy ? "Next message" : "Message"}</strong>
        <span role="status">
          {draft.replacement
            ? "Server restarted"
            : draft.synchronized
              ? "Synchronized in this instance"
              : draft.status === "Connected"
                ? "Synchronizing edits"
                : `${draft.status} · local edits retained`}
        </span>
      </div>
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
        draft={draft}
        profile={profile}
        presence={(value) => connection.current?.presence(value)}
        submit={() => void submit("send")}
      />
      {selections.length > 0 && (
        <ul className={styles.attachments}>
          {selections.map(([key, id], index) => (
            <li key={key}>
              <button
                type="button"
                onClick={() => void showAttachment(id, attachments[index].data)}
              >
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
                onClick={() => draft.doc.getMap("attachments").delete(key)}
              >
                <X />
              </Button>
              {attachments[index].error && (
                <span role="alert">
                  Attachment unavailable; remove it or refresh access.
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {(error || draft.error) && (
        <p role="alert" className={styles.warning}>
          {error || draft.error}
        </p>
      )}
      {draft.submission.kind !== "idle" &&
        draft.submission.kind !== "pending" && (
          <div role="status" className={styles.receipt}>
            <p>{draft.submission.message}</p>
            {"receipt" in draft.submission && draft.submission.receipt && (
              <small>Operation: {draft.submission.receipt}</small>
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
            onChange={(event) => void uploadFiles(event.target.files)}
          />
          <Button
            variant="ghost"
            loading={uploading}
            onClick={() => upload.current?.click()}
          >
            <Paperclip /> Attach files
          </Button>
        </div>
        <div>
          {busy && activity.available_actions?.includes("steer") && (
            <Button
              variant="outline"
              disabled={
                !draft.synchronized ||
                pending ||
                unknown ||
                !valid ||
                unsupportedSteer
              }
              onClick={() => void submit("steer")}
            >
              Send as instruction
            </Button>
          )}
          <Button
            disabled={!canSend}
            loading={pending}
            onClick={() => void submit("send")}
          >
            <ArrowUp />
            {pending ? "Submitting" : "Send"}
          </Button>
        </div>
      </div>
      <small className={styles.composerHint}>
        {busy ? "Next message is not queued. " : ""}Enter adds a line ·
        Ctrl/⌘+Enter sends · Drafts are not saved across server restarts.
      </small>
      {busy && unsupportedSteer && selections.length > 0 && (
        <small>
          Steering accepts only captured UTF-8 file/diff context up to 64 KiB
          each, not ordinary uploads. Remove unsupported selections or keep this
          for your next message.
        </small>
      )}
      <ModalFrame
        open={!!preview}
        onOpenChange={(open) => {
          if (!open) setPreview(null);
        }}
        title={preview?.name ?? "Attachment"}
        description="These are the retained bytes selected for input, not the current file on the server."
        closeLabel="Close"
      >
        <pre className={styles.code}>{preview?.text}</pre>
        {preview?.url && (
          <a href={preview.url} download={preview.name}>
            Download original
          </a>
        )}
      </ModalFrame>
    </section>
  );
}
