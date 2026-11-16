import {
  useContext,
  createContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useTranslation } from "../i18n";
import { Button, Popover, PopoverPopup, PopoverTrigger } from "a13n-ui";
import { Link } from "react-router";
import { ArrowUpRight, CaretDown, Chats } from "@phosphor-icons/react";
import { MessageActionButton, MessageBranches } from "./message-branch";
import { CopyMessage } from "./copy-message";
import { retainedSkillSpans, skillDisplayName } from "./skill-references";
import skillStyles from "./skill-chip.module.css";
import type { Schema } from "../transport/client";
import { useGuestAccess, useTransport } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import styles from "./conversation.module.css";
import { RetainedImagePreview } from "./image-preview";
import { AttachmentThumbnail } from "./attachment-thumbnail";

export const ChildInputLabels = createContext(false);

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
  const { t, i18n } = useTranslation();
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
  const details = (
    <Popover>
      <PopoverTrigger
        render={
          image ? (
            <Button variant="ghost" size="sm" />
          ) : (
            <button type="button" className={styles.inlineAttachmentOpen} />
          )
        }
        title={attachment.name}
      >
        <span>{image ? t("Attachment details") : attachment.name}</span>
      </PopoverTrigger>
      <PopoverPopup align="start">
        {" "}
        <div className={styles.inputAttachmentDetails}>
          <small>
            {attachment.media_type} -{" "}
            {t("{{size}} bytes", {
              size: attachment.size.toLocaleString(i18n.language),
            })}
          </small>
          {attachment.source && (
            <details>
              <summary>{t("Source and revision")}</summary>
              <pre className={styles.code}>
                {JSON.stringify(attachment.source, null, 2)}
              </pre>
            </details>
          )}
          {url ? (
            <a href={url} download={attachment.name}>
              {t("Download original bytes")}
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
              {t("Prepare download")}
            </Button>
          )}
          <details className={styles.rawSource}>
            <summary>{t("What the model sees")}</summary>
            {related.map((item, offset) => (
              <pre key={offset} className={styles.code}>
                {item.text || JSON.stringify(item.value, null, 2)}
              </pre>
            ))}
          </details>
          <ErrorNotice error={error} />
        </div>
      </PopoverPopup>
    </Popover>
  );
  return (
    <span className={image ? styles.sentImage : styles.inlineAttachment}>
      {image ? (
        <button
          type="button"
          onClick={() => setPreview(true)}
          aria-label={t("Preview {{name}}", { name: attachment.name })}
          title={attachment.name}
        >
          <AttachmentThumbnail threadId={threadId} attachment={attachment} />
          <span>{attachment.name}</span>
        </button>
      ) : (
        details
      )}
      {preview && (
        <RetainedImagePreview
          threadId={threadId}
          attachment={attachment}
          actions={details}
          close={() => setPreview(false)}
        />
      )}
    </span>
  );
}
function Media({ part }: { part: InputPart }) {
  const { t } = useTranslation();
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
        {part.text || t("Media content unavailable.")}
      </pre>
    );
  const url =
    typeof content.url === "string" && /^https?:\/\//i.test(content.url)
      ? content.url
      : undefined;
  return (
    <div className={styles.attachmentCard}>
      <strong>
        {typeof content.kind === "string" ? content.kind : t("Media")}
      </strong>
      <small>
        {typeof content.media_type === "string"
          ? content.media_type
          : t("Media reference")}
      </small>
      {url ? (
        <a href={url} target="_blank" rel="noopener noreferrer">
          {t("Open media source")}
        </a>
      ) : (
        <p>{t("Media bytes are not embedded in this transcript.")}</p>
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

function focusEnd(editor: HTMLTextAreaElement | null) {
  if (!editor) return;
  editor.focus();
  editor.setSelectionRange(editor.value.length, editor.value.length);
}

export function InputContent({
  parts,
  threadId,
  renderText,
  branchKey,
  timestamp,
  status,
}: {
  status?: string;
  timestamp?: string | number;
  branchKey?: string;
  parts: InputPart[];
  threadId?: string;
  renderText: (text: string) => ReactNode;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const contentId = useId();
  const guest = useGuestAccess();
  const childLabels = useContext(ChildInputLabels);
  const parentInput = parts.some(
    (part) => part.metadata?.["a13n.steering-source"] === "parent_agent",
  );
  const visible = parts.filter((part) => part.metadata?.display !== false);
  const seen = new Set<string>();
  const copyText = inputCopyText(parts);
  const actions = useContext(MessageBranches);
  const [edit, setEdit] = useState<string | null>(null);
  const editable =
    branchKey && actions?.points?.[branchKey]?.edit && !actions.busy;

  const source = visible
    .map((part) => threadMessage(part.metadata))
    .find(Boolean);
  const body =
    (!source || expanded) &&
    visible.map((part, index) => {
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
      if (part.kind !== "media" && !text) return null;
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
            title={t("Skill: {{name}}", { name: span.name })}
            data-skill={span.name}
          >
            {skillDisplayName(span.name)}
          </span>,
        );
        end = span.end;
      }
      marked.push(points.slice(end).join(""));
      if ((composerIdentity(part) || spans.length) && part.kind !== "media")
        return (
          <span
            key={index}
            className={styles.inputText}
            data-dictation-human={source ? undefined : ""}
          >
            {marked}
          </span>
        );
      return part.kind === "media" ? (
        <Media key={index} part={part} />
      ) : (
        <div key={index} data-dictation-human={source ? undefined : ""}>
          {renderText(part.text || "")}
        </div>
      );
    });
  if (edit !== null && actions)
    return (
      <form
        className={styles.messageEditor}
        onSubmit={(e) => {
          e.preventDefault();
          if (editable && branchKey && edit.trim())
            void actions.create(branchKey, edit);
        }}
      >
        <textarea
          ref={focusEnd}
          aria-label={t("Edit message")}
          value={edit}
          onChange={(e) => setEdit(e.target.value)}
          onKeyDown={(e) => {
            if (
              e.key === "Escape" &&
              !actions.pending &&
              !e.nativeEvent.isComposing
            )
              setEdit(null);
          }}
        />
        <div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={actions.pending}
            onClick={() => setEdit(null)}
          >
            {t("Cancel")}
          </Button>
          <Button type="submit" size="sm" disabled={!editable || !edit.trim()}>
            {t("Send")}
          </Button>
        </div>
      </form>
    );
  return (
    <div className={styles.userInput}>
      {(source || status || (body && body.some(Boolean))) && (
        <div
          className={`${styles.userMessage} ${source ? styles.threadMessage : ""}`}
        >
          {(source || status || childLabels || parentInput) && (
            <header>
              {(childLabels || parentInput) && (
                <span className={styles.inputAuthor}>
                  {parentInput ? t("Main agent") : t("User")}
                </span>
              )}
              {source && guest ? (
                <span className={styles.threadSource}>
                  {t("From thread")} - {source.title || source.id}
                </span>
              ) : source ? (
                <Link
                  className={styles.threadSource}
                  to={`/threads/${encodeURIComponent(source.id)}`}
                  title={
                    source.title ? `${source.title} - ${source.id}` : source.id
                  }
                  aria-label={t("From thread {{name}}", {
                    name: source.title || source.id,
                  })}
                >
                  <Chats size={16} aria-hidden="true" />
                  <span className={styles.threadSourceLabel}>
                    {t("From thread")}
                  </span>
                  <span className={styles.threadSourceName}>
                    {source.title || source.id}
                  </span>
                  <ArrowUpRight size={12} aria-hidden="true" />
                </Link>
              ) : null}
              {status && (
                <span role="status" className={styles.inputStatus}>
                  {status}
                </span>
              )}
            </header>
          )}
          {source ? (
            <div id={contentId} hidden={!expanded}>
              {body}
            </div>
          ) : (
            body
          )}
          {source && (
            <button
              type="button"
              className={styles.threadMessageToggle}
              aria-label={t("Message details")}
              aria-expanded={expanded}
              aria-controls={contentId}
              onClick={() => setExpanded(!expanded)}
            >
              {!expanded && copyText.trim() && (
                <span
                  className={styles.threadMessagePreview}
                  aria-hidden="true"
                >
                  {copyText}
                </span>
              )}
              <span className={styles.threadMessageToggleLabel}>
                {expanded ? t("Show less") : t("Show message")}
                <CaretDown size={14} aria-hidden="true" />
              </span>
            </button>
          )}
        </div>
      )}
      {(!source || expanded) && (copyText.trim() || timestamp != null) && (
        <CopyMessage
          text={copyText}
          timestamp={timestamp}
          action={
            actions &&
            branchKey !== undefined &&
            !source && (
              <MessageActionButton
                kind="edit"
                disabled={!editable}
                onClick={() => setEdit(copyText)}
              />
            )
          }
        />
      )}
    </div>
  );
}
