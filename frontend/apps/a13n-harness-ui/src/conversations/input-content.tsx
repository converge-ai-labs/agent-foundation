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
import {
  AttachmentPreviewSheet,
  attachmentTargetIdentity,
  triggerBlobDownload,
  type AttachmentPreviewTarget,
} from "./attachment-preview";
import {
  loadRetainedAttachment,
  prefetchRetainedAttachment,
} from "./attachment-cache";
import { isRasterImageAttachment } from "./attachment-preview-policy";

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
  onOpenPreview,
}: {
  threadId: string;
  attachment: Schema<"ThreadAttachment">;
  related: InputPart[];
  onOpenPreview?: () => void;
}) {
  const { t, i18n } = useTranslation();
  const transport = useTransport();
  const image = isRasterImageAttachment(attachment.media_type);
  const [preview, setPreview] = useState(false);
  const download = useRef<AbortController | null>(null);
  useEffect(
    () => () => download.current?.abort(),
    [transport, threadId, attachment.attachment_id],
  );
  const [error, setError] = useState<unknown>();
  const [loading, setLoading] = useState(false);

  if (!image) {
    const prefetch = () =>
      prefetchRetainedAttachment(transport, threadId, attachment);
    return (
      <span className={styles.inlineAttachment}>
        <button
          type="button"
          className={styles.inlineAttachmentOpen}
          title={attachment.name}
          onPointerEnter={prefetch}
          onFocus={prefetch}
          onClick={() => onOpenPreview?.()}
        >
          <span>{attachment.name}</span>
        </button>
      </span>
    );
  }

  const details = (
    <Popover>
      <PopoverTrigger
        render={<Button variant="ghost" size="sm" />}
        title={attachment.name}
      >
        <span>{t("Attachment details")}</span>
      </PopoverTrigger>
      <PopoverPopup align="start">
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
          <Button
            variant="ghost"
            loading={loading}
            onClick={() => {
              setLoading(true);
              setError(undefined);
              download.current?.abort();
              const controller = new AbortController();
              download.current = controller;
              void loadRetainedAttachment(
                transport,
                threadId,
                attachment.attachment_id,
                { signal: controller.signal },
              )
                .then((entry) => {
                  if (!controller.signal.aborted)
                    triggerBlobDownload(entry.blob, attachment.name);
                })
                .catch((failure) => {
                  if (!controller.signal.aborted) setError(failure);
                })
                .finally(() => {
                  if (!controller.signal.aborted) setLoading(false);
                });
            }}
          >
            {t("Download")}
          </Button>
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
    <span className={styles.sentImage}>
      <button
        type="button"
        onClick={() => setPreview(true)}
        aria-label={t("Preview {{name}}", { name: attachment.name })}
        title={attachment.name}
      >
        <AttachmentThumbnail threadId={threadId} attachment={attachment} />
        <span>{attachment.name}</span>
      </button>
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
function inputMessageText(parts: InputPart[], includeAttachments: boolean) {
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
      if (!includeAttachments) continue;
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

export function inputCopyText(parts: InputPart[]) {
  return inputMessageText(parts, false);
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
  const nonImageTargets: AttachmentPreviewTarget[] = [];
  const seenTargets = new Set<string>();
  if (threadId) {
    for (const part of visible) {
      const attachment = inputAttachment(part.metadata);
      if (!attachment || isRasterImageAttachment(attachment.media_type))
        continue;
      const identity = composerIdentity(part) ?? attachment.attachment_id;
      const targetKey = `retained:${threadId}:${attachment.attachment_id}`;
      if (seenTargets.has(targetKey)) continue;
      seenTargets.add(targetKey);
      const related = visible.filter(
        (item) =>
          (composerIdentity(item) ??
            inputAttachment(item.metadata)?.attachment_id) === identity,
      );
      nonImageTargets.push({
        kind: "retained",
        threadId,
        attachmentId: attachment.attachment_id,
        name: attachment.name,
        mediaType: attachment.media_type,
        size: attachment.size,
        source: attachment.source,
        related,
      });
    }
  }
  const [previewTargetKey, setPreviewTargetKey] = useState<string | null>(null);
  const activePreviewTarget = previewTargetKey
    ? (nonImageTargets.find(
        (item) => attachmentTargetIdentity(item) === previewTargetKey,
      ) ?? null)
    : null;
  const copyText = inputCopyText(parts);
  const actions = useContext(MessageBranches);
  const [edit, setEdit] = useState<string | null>(null);
  const editable =
    branchKey && actions?.points?.[branchKey]?.edit && !actions.busy;

  const showActions = copyText.trim() || timestamp != null || editable;

  const source = visible
    .map((part) => threadMessage(part.metadata))
    .find(Boolean);
  const preview = source && !expanded ? inputMessageText(parts, true) : "";
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
            onOpenPreview={() =>
              setPreviewTargetKey(
                `retained:${threadId}:${attachment.attachment_id}`,
              )
            }
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
              {preview.trim() && (
                <span
                  className={styles.threadMessagePreview}
                  aria-hidden="true"
                >
                  {preview}
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
      {(!source || expanded) && showActions && (
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
                onClick={() => setEdit(inputMessageText(parts, true))}
              />
            )
          }
        />
      )}
      {nonImageTargets.length > 0 && (
        <AttachmentPreviewSheet
          target={activePreviewTarget}
          items={nonImageTargets}
          onSelect={(next) =>
            setPreviewTargetKey(attachmentTargetIdentity(next))
          }
          onClose={() => setPreviewTargetKey(null)}
        />
      )}
    </div>
  );
}
