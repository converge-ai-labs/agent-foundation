import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Button } from "a13n-ui";
import { Link } from "react-router";
import { ArrowUpRight, CaretDown, Chats } from "@phosphor-icons/react";
import { CopyMessage } from "./copy-message";
import { retainedSkillSpans } from "./skill-references";
import skillStyles from "./skill-chip.module.css";
import type { Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import styles from "./conversation.module.css";
import { RetainedImagePreview } from "./image-preview";
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
function threadMessage(metadata: InputPart["metadata"]) {
  const ui = metadata?.harness_ui;
  const source = object(ui) ? ui.thread_message : undefined;
  if (
    !object(source) ||
    typeof source.source_thread_id !== "string" ||
    !source.source_thread_id.trim()
  )
    return undefined;
  return {
    id: source.source_thread_id,
    title:
      typeof source.source_thread_title === "string"
        ? source.source_thread_title.trim()
        : "",
  };
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
  const image = attachment.media_type.startsWith("image/");
  const [preview, setPreview] = useState(false);
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
    <div className={styles.inputAttachment}>
      {image && (
        <button
          type="button"
          className={styles.imageAttachment}
          onClick={() => setPreview(true)}
          aria-label={`Preview ${attachment.name}`}
        >
          <AttachmentThumbnail threadId={threadId} attachment={attachment} />
          <strong>{attachment.name}</strong>
        </button>
      )}
      <details>
        <summary>
          {image ? (
            "Attachment details"
          ) : (
            <>
              <AttachmentThumbnail
                threadId={threadId}
                attachment={attachment}
              />
              <strong>{attachment.name}</strong>
            </>
          )}
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
      {preview && (
        <RetainedImagePreview
          threadId={threadId}
          attachment={attachment}
          close={() => setPreview(false)}
        />
      )}
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
export function inputCopyText(parts: InputPart[]) {
  const seen = new Set<string>();
  let text = "";
  let previousInline = false;
  for (const part of parts) {
    if (
      part.metadata?.display === false ||
      !["user", "media"].includes(part.kind)
    )
      continue;
    const composer = composerIdentity(part);
    const attachment = inputAttachment(part.metadata);
    let value = part.kind === "user" ? (part.text ?? "") : "";
    if (attachment) {
      const identity = composer ?? attachment.attachment_id;
      if (seen.has(identity)) continue;
      seen.add(identity);
      value = `[${attachment.name}]`;
    }
    if (!value) continue;
    // Composer parts are slices of one authored document, not paragraphs.
    if (text && !(previousInline && composer)) text += "\n\n";
    text += value;
    previousInline = !!composer;
  }
  return text;
}

export function InputContent({
  parts,
  threadId,
  renderText,
  status,
}: {
  status?: string;
  parts: InputPart[];
  threadId?: string;
  renderText: (text: string) => ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const contentId = useId();
  const visible = parts.filter((part) => part.metadata?.display !== false);
  const seen = new Set<string>();
  const copyText = inputCopyText(parts);
  const source = visible
    .map((part) => threadMessage(part.metadata))
    .find(Boolean);
  const content = (!source || expanded) && (
    <>
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
        const text = part.text || "";
        const spans = retainedSkillSpans(text, part.metadata?.harness_ui);
        const points = [...text];
        let end = 0;
        const marked: ReactNode[] = [];
        for (const span of spans) {
          marked.push(points.slice(end, span.start).join(""));
          marked.push(
            <span
              key={span.start}
              className={skillStyles.chip}
              title={`Skill: ${span.name}`}
              data-skill={span.name}
            >
              {points.slice(span.start, span.end).join("")}
            </span>,
          );
          end = span.end;
        }
        marked.push(points.slice(end).join(""));
        if ((composerIdentity(part) || spans.length) && part.kind !== "media")
          return (
            <span key={index} className={styles.inputText}>
              {marked}
            </span>
          );
        return part.kind === "media" ? (
          <Media key={index} part={part} />
        ) : (
          <div key={index}>{renderText(part.text || "")}</div>
        );
      })}
    </>
  );
  return (
    <div className={styles.userInput}>
      <div
        className={`${styles.userMessage} ${source ? styles.threadMessage : ""}`}
      >
        <header>
          {source ? (
            <Link
              className={styles.threadSource}
              to={`/threads/${encodeURIComponent(source.id)}`}
              title={
                source.title ? `${source.title} · ${source.id}` : source.id
              }
              aria-label={`From thread ${source.title || source.id}`}
            >
              <Chats size={16} aria-hidden="true" />
              <span className={styles.threadSourceLabel}>From thread</span>
              <span className={styles.threadSourceName}>
                {source.title || source.id}
              </span>
              <ArrowUpRight size={12} aria-hidden="true" />
            </Link>
          ) : (
            "User"
          )}
          {status && (
            <span role="status" className={styles.inputStatus}>
              {status}
            </span>
          )}
        </header>
        {source ? (
          <div id={contentId} hidden={!expanded}>
            {content}
          </div>
        ) : (
          content
        )}
        {source && (
          <button
            type="button"
            className={styles.threadMessageToggle}
            aria-label="Thread message details"
            aria-expanded={expanded}
            aria-controls={contentId}
            onClick={() => setExpanded(!expanded)}
          >
            {!expanded && copyText.trim() && (
              <span className={styles.threadMessagePreview} aria-hidden="true">
                {copyText}
              </span>
            )}
            <span className={styles.threadMessageToggleLabel}>
              {expanded ? "Show less" : "Show message"}
              <CaretDown size={14} aria-hidden="true" />
            </span>
          </button>
        )}
      </div>
      {(!source || expanded) && copyText.trim() && (
        <CopyMessage text={copyText} />
      )}
    </div>
  );
}
