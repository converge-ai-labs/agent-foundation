import { useEffect, useRef, useState, type ReactNode } from "react";
import { Button } from "a13n-ui";
import type { Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import styles from "./conversation.module.css";

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
}: {
  threadId: string;
  attachment: Schema<"ThreadAttachment">;
}) {
  const transport = useTransport();
  const [url, setUrl] = useState("");
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const [error, setError] = useState<unknown>();
  const [loading, setLoading] = useState(false);
  useEffect(
    () => () => {
      if (url) URL.revokeObjectURL(url);
    },
    [url],
  );
  return (
    <div className={styles.attachmentCard}>
      <strong>{attachment.name}</strong>
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
            void transport
              .fetch(
                `/api/threads/${encodeURIComponent(threadId)}/attachments/${encodeURIComponent(attachment.attachment_id)}`,
              )
              .then((response) => response.blob())
              .then((blob) => {
                if (mounted.current) setUrl(URL.createObjectURL(blob));
              })
              .catch(setError)
              .finally(() => setLoading(false));
          }}
        >
          Prepare download
        </Button>
      )}
      <ErrorNotice error={error} />
    </div>
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
          if (seen.has(attachment.attachment_id)) return null;
          seen.add(attachment.attachment_id);
          const related = visible.filter(
            (item) =>
              inputAttachment(item.metadata)?.attachment_id ===
              attachment.attachment_id,
          );
          return (
            <section key={index}>
              <Attachment threadId={threadId} attachment={attachment} />
              <details className={styles.rawSource}>
                <summary>Model-visible attachment content</summary>
                {related.map((item, offset) => (
                  <pre key={offset} className={styles.code}>
                    {item.text || JSON.stringify(item.value, null, 2)}
                  </pre>
                ))}
              </details>
            </section>
          );
        }
        return part.kind === "media" ? (
          <Media key={index} part={part} />
        ) : (
          <div key={index}>{renderText(part.text || "")}</div>
        );
      })}
    </div>
  );
}
