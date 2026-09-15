import { useContext, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { ComposerDrafts } from "../conversations/composer";
import { attachmentSelections } from "../conversations/inline-attachments";
import { result, type Schema, type Transport } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice, TextField } from "../shell/ui";
import { lineRange, supportsLineRanges, type LineRange } from "./buffer";
import styles from "./native.module.css";

export type CaptureSource =
  { file: Schema<"FileText"> } | { diff: Schema<"GitDiff"> };
export function captureSource(
  transport: Transport,
  threadId: string,
  source: CaptureSource,
  range?: LineRange,
) {
  if ("file" in source)
    return result(
      transport.client.POST("/api/threads/{thread_id}/host-file-captures", {
        params: { path: { thread_id: threadId } },
        body: {
          path: source.file.entry.path,
          expected_revision: source.file.entry.revision,
          ...range,
        },
      }),
    );
  return result(
    transport.client.POST("/api/threads/{thread_id}/host-git-captures", {
      params: { path: { thread_id: threadId } },
      body: {
        repository_path: source.diff.repository.root,
        path: source.diff.path,
        comparison: source.diff.comparison,
        expected_revision: source.diff.revision,
        ...range,
      },
    }),
  );
}
export function CaptureContext({
  source,
  threadId,
  disabled,
  selection,
}: {
  source: CaptureSource;
  threadId?: string;
  disabled?: boolean;
  selection?: LineRange;
}) {
  const transport = useTransport();
  const drafts = useContext(ComposerDrafts);
  const queries = useQueryClient();
  const [range, setRange] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [message, setMessage] = useState("");
  const text =
    "diff" in source
      ? source.diff.presentation === "text"
      : source.file.presentation === "text";
  const rangesSupported =
    text &&
    supportsLineRanges(
      ("diff" in source ? source.diff.text : source.file.text) ?? "",
    );
  const capture = async (selectedLines?: LineRange) => {
    const draft = threadId ? drafts.get(threadId) : undefined;
    if (!threadId || !draft) return;
    setPending(true);
    setError(null);
    setMessage("");
    try {
      if (!draft.synchronized || draft.replacement)
        throw new Error(
          "Reconnect the conversation's shared input before capturing context.",
        );
      if (attachmentSelections(draft.doc).length >= 8)
        throw new Error("Remove an attachment first (limit: eight).");
      const incarnation = draft.draftId;
      const captured = await captureSource(
        transport,
        threadId,
        source,
        selectedLines ?? (range ? lineRange(start, end) : undefined),
      );
      if (draft.draftId !== incarnation || draft.replacement)
        throw new Error(
          "The shared draft was replaced during capture. Rejoin before adding context again.",
        );
      if (attachmentSelections(draft.doc).length >= 8)
        throw new Error(
          "The shared input now has eight attachments. Remove one before adding context again.",
        );
      const attachment = captured.attachment;
      queries.setQueryData(
        ["thread", threadId, "attachment", attachment.attachment_id],
        attachment,
      );
      draft.addAttachment(attachment.attachment_id);
      setMessage(
        "Added to the conversation. Return to Chat to review it before sending.",
      );
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  };
  return (
    <section className={styles.capture} aria-label="Capture reviewed context">
      <div className={styles.actions}>
        <Button
          variant="outline"
          size="sm"
          disabled={!threadId || disabled || pending}
          onClick={() => void capture()}
          loading={pending}
        >
          {range ? "Add selected lines to prompt" : "Add to prompt"}
        </Button>
        {rangesSupported && (
          <label className={styles.check}>
            <input
              type="checkbox"
              checked={range}
              onChange={(event) => setRange(event.target.checked)}
            />
            Choose line range
          </label>
        )}
        {rangesSupported && selection && (
          <Button
            variant="ghost"
            size="sm"
            disabled={!threadId || disabled || pending}
            onClick={() => void capture(selection)}
          >
            Add selection to prompt
          </Button>
        )}
      </div>
      {range && (
        <div className={styles.range}>
          <TextField
            label={"diff" in source ? "First patch line" : "First line"}
            value={start}
            onChange={setStart}
          />
          <TextField
            label="Last line (inclusive)"
            value={end}
            onChange={setEnd}
          />
        </div>
      )}
      {text && !rangesSupported && (
        <small>
          This source contains nonstandard line separators. Use whole-source
          capture; editor line numbers cannot identify the API's lines exactly.
        </small>
      )}
      <small>
        {threadId
          ? "Adds a saved copy to this conversation; it does not send a message."
          : "Open a conversation to add file context."}
      </small>
      {disabled && (
        <small>
          Save and refresh your file before capturing its reviewed disk content.
        </small>
      )}
      {message && <p role="status">{message}</p>}
      <ErrorNotice error={error} />
    </section>
  );
}

export function downloadBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
