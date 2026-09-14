import { useEffect, useRef, useState, type ReactNode } from "react";
import { Button } from "a13n-ui";
import type { Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import styles from "./conversation.module.css";
import { AttachmentThumbnail } from "./attachment-thumbnail";

export type InputPart = {
  kind: string;
  text?: string | null;
  value?: unknown;
  metadata?: Record<string, unknown> | null;
};
function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
export function inputAttachment(metadata: InputPart["metadata"]) {
  const ui = metadata?.harness_ui;
  const attachment = object(ui) ? ui.attachment : undefined;
  return object(attachment) &&
    typeof attachment.attachment_id === "string" &&
    typeof attachment.name === "string" &&
    typeof attachment.media_type === "string" &&
    typeof attachment.size === "number"
    ? (attachment as Schema<"ThreadAttachment">)
    : undefined;
}
function Attachment({
  threadId,
  attachment,
  related,
}: {
  threadId: string;
  attachment: Schema<"ThreadAttachment">;
  related: InputPart[];
}) {
  const transport = useTransport();
  const [url, setUrl] = useState("");
  const download = useRef<AbortController | null>(null);
  useEffect(
    () => () => download.current?.abort(),
    [transport, threadId, attachment.attachment_id],
  );
  const [error, setError] = useState<unknown>();
  const [loading, setLoading] = useState(false);
  useEffect(
    () => () => {
      if (url) URL.revokeObjectURL(url);
    },
    [url],
  );
  return (
    <details className={styles.inputAttachment}>
      <summary>
        <AttachmentThumbnail threadId={threadId} attachment={attachment} />
        <strong>
          {attachment.source && "comment_id" in attachment.source
            ? "Comment reference · "
            : ""}
          {attachment.name}
        </strong>
      </summary>
      <div className={styles.inputAttachmentDetails}>
        <small>
          {attachment.media_type} · {attachment.size.toLocaleString()} bytes
        </small>
        {attachment.source && (
          <details>
            <summary>Captured source and revision</summary>
            <pre className={styles.code}>
              {JSON.stringify(attachment.source, null, 2)}
            </pre>
          </details>
        )}
        {url ? (
          <a href={url} download={attachment.name}>
            Download original bytes
          </a>
        ) : (
          <Button
            variant="ghost"
            loading={loading}
            onClick={() => {
              setLoading(true);
              setError(undefined);
              download.current?.abort();
              const controller = new AbortController();
              download.current = controller;
              void transport
                .fetch(
                  `/api/threads/${encodeURIComponent(threadId)}/attachments/${encodeURIComponent(attachment.attachment_id)}`,
                  { signal: controller.signal },
                )
                .then((response) => response.blob())
                .then((blob) => {
                  if (!controller.signal.aborted)
                    setUrl(URL.createObjectURL(blob));
                })
                .catch((failure) => {
                  if (!controller.signal.aborted) setError(failure);
                })
                .finally(() => {
                  if (!controller.signal.aborted) setLoading(false);
                });
            }}
          >
            Prepare download
          </Button>
        )}
        <details className={styles.rawSource}>
          <summary>Model-visible attachment content</summary>
          {related.map((item, offset) => (
            <pre key={offset} className={styles.code}>
              {item.text || JSON.stringify(item.value, null, 2)}
            </pre>
          ))}
        </details>
        <ErrorNotice error={error} />
      </div>
    </details>
  );
}
function Media({ part }: { part: InputPart }) {
  let content = part.value;
  if (content === undefined && part.text) {
    try {
      content = JSON.parse(part.text);
    } catch {
      /* TextContent can deliberately be marked as media. */
    }
  }
  if (!object(content))
    return (
      <pre className={styles.code}>
        {part.text || "Media content unavailable."}
      </pre>
    );
  const url =
    typeof content.url === "string" && /^https?:\/\//i.test(content.url)
      ? content.url
      : undefined;
  return (
    <div className={styles.attachmentCard}>
      <strong>
        {typeof content.kind === "string" ? content.kind : "Media"}
      </strong>
      <small>
        {typeof content.media_type === "string"
          ? content.media_type
          : "Media reference"}
      </small>
      {url ? (
        <a href={url} target="_blank" rel="noopener noreferrer">
          Open media source
        </a>
      ) : (
        <p>Media bytes are not embedded in this transcript.</p>
      )}
    </div>
  );
}
function composerIdentity(part: InputPart) {
  const ui = part.metadata?.harness_ui;
  const composer = object(ui) ? ui.composer : undefined;
  return typeof part.metadata?.source_id === "string" &&
    object(composer) &&
    typeof composer.index === "number" &&
    Number.isInteger(composer.index) &&
    composer.index >= 0
    ? `${part.metadata.source_id}:${composer.index}`
    : undefined;
}
export function InputContent({
  parts,
  threadId,
  renderText,
}: {
  parts: InputPart[];
  threadId?: string;
  renderText: (text: string) => ReactNode;
}) {
  const visible = parts.filter((part) => part.metadata?.display !== false);
  const seen = new Set<string>();
  return (
    <div className={styles.userMessage}>
      <header>Input</header>
      {visible.map((part, index) => {
        const attachment = inputAttachment(part.metadata);
        if (attachment && threadId) {
          const identity = composerIdentity(part) ?? attachment.attachment_id;
          if (seen.has(identity)) return null;
          seen.add(identity);
          const related = visible.filter(
            (item) =>
              (composerIdentity(item) ??
                inputAttachment(item.metadata)?.attachment_id) === identity,
          );
          return (
            <Attachment
              key={`${threadId}:${identity}`}
              threadId={threadId}
              attachment={attachment}
              related={related}
            />
          );
        }
        if (composerIdentity(part) && part.kind !== "media")
          return (
            <span key={index} className={styles.inputText}>
              {part.text || ""}
            </span>
          );
        return part.kind === "media" ? (
          <Media key={index} part={part} />
        ) : (
          <div key={index}>{renderText(part.text || "")}</div>
        );
      })}
    </div>
  );
}
