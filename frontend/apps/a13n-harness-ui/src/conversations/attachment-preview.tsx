import { useEffect, useRef, useState } from "react";
import {
  Button,
  FileTypeIcon,
  Sheet,
  SheetClose,
  SheetDescription,
  SheetPopup,
  SheetTitle,
  Tooltip,
  TooltipPopup,
  TooltipTrigger,
} from "a13n-ui";
import {
  ArrowBendDownLeft,
  Check,
  Copy,
  DownloadSimple,
  MagnifyingGlass,
  X,
} from "@phosphor-icons/react";
import { useTranslation } from "../i18n";
import type { Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { SourceEditor } from "../configuration/editor";
import type { SourceEditorHandle } from "../configuration/editor-view";
import { DocumentPreview } from "../native/document-preview";
import {
  MAX_MEDIA_BYTES,
  mediaKind,
  type MediaKind,
} from "../native/media-kind";
import { copyText, modifierLabel } from "../platform";
import { isFindShortcut } from "../platform/find-shortcut";
import { ErrorNotice } from "../shell/ui";
import { attachmentPath } from "./attachment-thumbnail";
import { MessageText } from "./message-text";
import {
  classifyAttachmentPreview,
  decodeUtf8Text,
  formatAttachmentSize,
  TEXT_PREVIEW_LIMIT,
} from "./attachment-preview-policy";
import styles from "./attachment-preview.module.css";

export type AttachmentRelatedPart = {
  kind: string;
  text?: string | null;
  value?: unknown;
};

export type AttachmentPreviewTarget =
  | {
      kind: "retained";
      threadId: string;
      attachmentId: string;
      name: string;
      mediaType?: string;
      size?: number;
      source?: Schema<"ThreadAttachment">["source"];
      related?: AttachmentRelatedPart[];
      snapshotNotice?: boolean;
    }
  | {
      kind: "local";
      file: File;
    };

export function triggerBlobDownload(
  blob: Blob,
  filename: string,
  onPendingUrl?: (revoke: () => void) => void,
) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noreferrer";
  anchor.referrerPolicy = "no-referrer";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  let revoked = false;
  const revoke = () => {
    if (revoked) return;
    revoked = true;
    URL.revokeObjectURL(url);
  };
  const timer = setTimeout(revoke, 0);
  onPendingUrl?.(() => {
    clearTimeout(timer);
    revoke();
  });
}

type PreviewState =
  | { status: "loading" }
  | { status: "text"; text: string; markdown: boolean; svg: boolean }
  | { status: "document"; documentKind: "pdf" | "sheet" | "slides" }
  | { status: "media"; mediaKind: MediaKind; url: string }
  | { status: "binary" }
  | { status: "unsupported" }
  | {
      status: "too-large";
      category: "text" | "document" | "audio" | "video" | "image";
    }
  | { status: "error"; error: unknown };

function targetIdentity(target: AttachmentPreviewTarget): string {
  return target.kind === "retained"
    ? `retained:${target.threadId}:${target.attachmentId}`
    : `local:${target.file.name}:${target.file.size}:${target.file.lastModified}`;
}

export function AttachmentPreviewSheet({
  target,
  onClose,
}: {
  target: AttachmentPreviewTarget | null;
  onClose: () => void;
}) {
  return (
    <Sheet
      open={!!target}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      {target && (
        <AttachmentPreviewBody key={targetIdentity(target)} target={target} />
      )}
    </Sheet>
  );
}

function AttachmentPreviewBody({
  target,
}: {
  target: AttachmentPreviewTarget;
}) {
  const { t, i18n } = useTranslation();
  const transport = useTransport();
  const key = targetIdentity(target);
  const name = target.kind === "retained" ? target.name : target.file.name;
  const initialMediaType =
    target.kind === "retained"
      ? target.mediaType
      : target.file.type || undefined;
  const initialSize =
    target.kind === "retained" ? target.size : target.file.size;
  const source = target.kind === "retained" ? target.source : undefined;
  const related = target.kind === "retained" ? target.related : undefined;
  const snapshotNotice =
    target.kind === "retained" ? !!target.snapshotNotice : false;

  const [resolvedSize, setResolvedSize] = useState<number | undefined>(
    initialSize,
  );
  const [resolvedMediaType, setResolvedMediaType] = useState<
    string | undefined
  >(initialMediaType);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<PreviewState>(() => {
    const plan = classifyAttachmentPreview({
      name,
      mediaType: initialMediaType,
      size: initialSize,
    });
    if (plan.kind === "unsupported") return { status: "unsupported" };
    if (plan.kind === "too-large")
      return { status: "too-large", category: plan.category };
    if (plan.kind === "document")
      return { status: "document", documentKind: plan.documentKind };
    return { status: "loading" };
  });

  const [wrap, setWrap] = useState(() =>
    /\.(?:md|markdown|txt|log|csv)$/i.test(name),
  );
  const [chosenMode, setChosenMode] = useState<"preview" | "text">();
  const [textOpened, setTextOpened] = useState(false);
  const [mediaDecodeError, setMediaDecodeError] = useState(false);
  const [copyState, setCopyState] = useState<
    "idle" | "copying" | "copied" | "error"
  >("idle");
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<unknown>();

  const editor = useRef<SourceEditorHandle>(null);
  const player = useRef<HTMLMediaElement | null>(null);
  const blobCache = useRef<{ key: string; blob: Blob } | null>(null);
  const downloadController = useRef<AbortController | null>(null);
  const pendingRevokes = useRef(new Set<() => void>());

  useEffect(() => {
    const revokes = pendingRevokes.current;
    return () => {
      downloadController.current?.abort();
      for (const revoke of revokes) revoke();
      revokes.clear();
    };
  }, []);

  useEffect(() => {
    if (copyState !== "copied") return;
    const timer = setTimeout(() => setCopyState("idle"), 2000);
    return () => clearTimeout(timer);
  }, [copyState]);

  const loadBlob = async (signal?: AbortSignal): Promise<Blob> => {
    if (target.kind === "local") return target.file;
    if (blobCache.current?.key === key) return blobCache.current.blob;
    const response = await transport.fetch(
      attachmentPath(target.threadId, target.attachmentId),
      { signal },
    );
    const blob = await response.blob();
    if (!signal?.aborted) {
      blobCache.current = { key, blob };
      if (initialSize === undefined) setResolvedSize(blob.size);
      if (!initialMediaType && blob.type) setResolvedMediaType(blob.type);
    }
    return blob;
  };

  useEffect(() => {
    const plan = classifyAttachmentPreview({
      name,
      mediaType: initialMediaType,
      size: initialSize,
    });
    if (plan.kind === "unsupported") {
      setState({ status: "unsupported" });
      return;
    }
    if (plan.kind === "too-large") {
      setState({ status: "too-large", category: plan.category });
      return;
    }
    if (plan.kind === "document") {
      setState({ status: "document", documentKind: plan.documentKind });
      return;
    }
    const controller = new AbortController();
    let mediaUrl: string | undefined;
    setState({ status: "loading" });
    setMediaDecodeError(false);

    (async () => {
      const blob = await loadBlob(controller.signal);
      if (controller.signal.aborted) return;

      const effectiveMedia =
        plan.kind === "media"
          ? plan.mediaKind
          : !initialMediaType && !/\.ts$/i.test(name)
            ? mediaKind(blob.type)
            : null;
      if (effectiveMedia) {
        if (blob.size > MAX_MEDIA_BYTES) {
          setState({ status: "too-large", category: effectiveMedia });
          return;
        }
        mediaUrl = URL.createObjectURL(blob);
        setState({
          status: "media",
          mediaKind: effectiveMedia,
          url: mediaUrl,
        });
        return;
      }

      if (blob.size > TEXT_PREVIEW_LIMIT) {
        setState({ status: "too-large", category: "text" });
        return;
      }
      const bytes = await blob.arrayBuffer();
      if (controller.signal.aborted) return;
      const decoded = decodeUtf8Text(bytes);
      if (decoded === null) {
        setState({ status: "binary" });
        return;
      }
      setState({
        status: "text",
        text: decoded,
        markdown: plan.kind === "text" && plan.markdown,
        svg:
          (plan.kind === "text" && plan.svg) || blob.type === "image/svg+xml",
      });
    })().catch((failure: unknown) => {
      if (!controller.signal.aborted)
        setState({ status: "error", error: failure });
    });

    return () => {
      controller.abort();
      if (mediaUrl) URL.revokeObjectURL(mediaUrl);
    };
  }, [transport, key, name, initialMediaType, initialSize, attempt]);

  useEffect(() => {
    const element = player.current;
    return () => {
      if (element) {
        element.pause();
        element.removeAttribute("src");
        element.load();
      }
    };
  }, [state]);

  const handleDownload = async () => {
    setDownloading(true);
    setDownloadError(undefined);
    downloadController.current?.abort();
    const controller = new AbortController();
    downloadController.current = controller;
    try {
      const blob = await loadBlob(controller.signal);
      if (controller.signal.aborted) return;
      triggerBlobDownload(blob, name, (revoke) => {
        pendingRevokes.current.add(revoke);
      });
    } catch (failure) {
      if (!controller.signal.aborted) setDownloadError(failure);
    } finally {
      if (!controller.signal.aborted) setDownloading(false);
    }
  };

  const isText = state.status === "text";
  const hasRichPreview = isText && (state.markdown || state.svg);
  const mode = chosenMode ?? (hasRichPreview ? "preview" : "text");
  const showSourceToolbar = isText && mode === "text";

  const copyLabel = copyState === "copied" ? t("Copied") : t("Copy");
  const formattedSize =
    resolvedSize !== undefined
      ? formatAttachmentSize(resolvedSize, i18n.language)
      : "";
  const exactSizeTitle =
    resolvedSize !== undefined
      ? t("{{size}} bytes", {
          size: resolvedSize.toLocaleString(i18n.language),
        })
      : undefined;
  const metaText = [resolvedMediaType, formattedSize]
    .filter(Boolean)
    .join(" · ");

  const tooLargeMessage =
    state.status === "too-large"
      ? state.category === "document"
        ? t("Document exceeds the 16 MiB preview limit.")
        : state.category === "text"
          ? t(
              "Preview text files up to 512 KiB without NUL bytes. Download the file to view it.",
            )
          : t("Media exceeds the 10 MiB preview limit.")
      : "";

  const mediaLabel =
    state.status === "media"
      ? state.mediaKind === "image"
        ? t("image")
        : state.mediaKind === "audio"
          ? t("audio")
          : t("video")
      : "";

  return (
    <SheetPopup
      side="right"
      showCloseButton={false}
      className={styles.drawer}
      onKeyDownCapture={(event) => {
        if (
          !showSourceToolbar ||
          !editor.current ||
          !isFindShortcut(event.nativeEvent) ||
          (event.target instanceof Element &&
            event.target.closest(".cm-editor"))
        )
          return;
        event.preventDefault();
        event.stopPropagation();
        editor.current.find();
      }}
    >
      <header className={styles.header}>
        <div className={styles.identity}>
          <span className={styles.fileIcon} aria-hidden="true">
            <FileTypeIcon name={name} size={20} />
          </span>
          <div className={styles.headingText}>
            <SheetTitle className={styles.title} title={name}>
              {name}
            </SheetTitle>
            <SheetDescription className={styles.meta} title={exactSizeTitle}>
              {metaText || t("Attachment preview")}
            </SheetDescription>
          </div>
        </div>
        <SheetClose
          aria-label={t("Close")}
          className={styles.closeButton}
          render={<Button size="icon-sm" variant="ghost" />}
        >
          <X />
        </SheetClose>
      </header>

      <div className={styles.toolbar}>
        {hasRichPreview && (
          <div
            className={styles.viewControls}
            aria-label={t("Attachment view")}
          >
            <Button
              variant={mode === "preview" ? "secondary" : "ghost"}
              size="sm"
              aria-pressed={mode === "preview"}
              onClick={() => setChosenMode("preview")}
            >
              {t("Preview")}
            </Button>
            <Button
              variant={mode === "text" ? "secondary" : "ghost"}
              size="sm"
              aria-pressed={mode === "text"}
              onClick={() => {
                setChosenMode("text");
                setTextOpened(true);
              }}
            >
              {t("Text")}
            </Button>
          </div>
        )}
        <div className={styles.actions} aria-label={t("Attachment actions")}>
          {showSourceToolbar && (
            <>
              <Tooltip>
                <TooltipTrigger
                  render={
                    <Button
                      size="icon-sm"
                      variant={wrap ? "secondary" : "ghost"}
                    />
                  }
                  aria-label={t("Wrap long lines")}
                  aria-pressed={wrap}
                  onClick={() => {
                    setWrap((current) => !current);
                    editor.current?.focus();
                  }}
                >
                  <ArrowBendDownLeft />
                </TooltipTrigger>
                <TooltipPopup>{t("Wrap long lines")}</TooltipPopup>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger
                  render={<Button size="icon-sm" variant="ghost" />}
                  aria-label={t("Find")}
                  onClick={() => editor.current?.find()}
                >
                  <MagnifyingGlass />
                </TooltipTrigger>
                <TooltipPopup>
                  {t("Find in file ({{shortcut}} F)", {
                    shortcut: modifierLabel(),
                  })}
                </TooltipPopup>
              </Tooltip>
            </>
          )}
          {isText && (
            <Button
              variant="ghost"
              size="sm"
              disabled={copyState === "copying"}
              aria-label={copyLabel}
              onClick={async () => {
                setCopyState("copying");
                try {
                  await copyText(state.text);
                  setCopyState("copied");
                } catch {
                  setCopyState("error");
                }
              }}
            >
              {copyState === "copied" ? (
                <Check aria-hidden="true" />
              ) : (
                <Copy aria-hidden="true" />
              )}
              <span>{copyLabel}</span>
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            loading={downloading}
            onClick={() => void handleDownload()}
          >
            <DownloadSimple aria-hidden="true" />
            <span>{t("Download")}</span>
          </Button>
        </div>
      </div>

      <span className="sr-only" aria-live="polite">
        {copyState === "copied" ? t("Copied to clipboard") : ""}
      </span>
      {copyState === "error" && (
        <p className={styles.copyError} role="alert">
          {t("Could not copy. Try again or select the text to copy manually.")}
        </p>
      )}

      <div className={`${styles.body} a13n-scrollbar`}>
        {snapshotNotice && (
          <p className={styles.snapshotNote}>
            {t(
              "This is the attached snapshot. The original file may have changed.",
            )}
          </p>
        )}

        <ErrorNotice error={downloadError} />

        {state.status === "loading" && (
          <div
            className={styles.loading}
            role="status"
            aria-label={t("Reading file…")}
          >
            <span className={styles.loadingMark} aria-hidden="true">
              <FileTypeIcon name={name} size={24} />
            </span>
            <span>{t("Loading…")}</span>
          </div>
        )}

        {state.status === "error" && (
          <ErrorNotice
            error={state.error}
            retry={() => setAttempt((value) => value + 1)}
          />
        )}

        {state.status === "unsupported" && (
          <div className={styles.statusCard} role="status">
            <span className={styles.statusIcon} aria-hidden="true">
              <FileTypeIcon name={name} size={26} />
            </span>
            <strong>{t("Preview not supported")}</strong>
            <p>{t("Download the original to open it in another app.")}</p>
            <Button
              variant="outline"
              size="sm"
              loading={downloading}
              onClick={() => void handleDownload()}
            >
              <DownloadSimple aria-hidden="true" />
              <span>{t("Download")}</span>
            </Button>
          </div>
        )}

        {state.status === "binary" && (
          <div className={styles.statusCard} role="status">
            <span className={styles.statusIcon} aria-hidden="true">
              <FileTypeIcon name={name} size={26} />
            </span>
            <strong>{t("Preview not supported")}</strong>
            <p>
              {t("Binary attachment. Download to inspect its original bytes.")}
            </p>
            <Button
              variant="outline"
              size="sm"
              loading={downloading}
              onClick={() => void handleDownload()}
            >
              <DownloadSimple aria-hidden="true" />
              <span>{t("Download")}</span>
            </Button>
          </div>
        )}

        {state.status === "too-large" && (
          <div className={styles.statusCard} role="status">
            <span className={styles.statusIcon} aria-hidden="true">
              <FileTypeIcon name={name} size={26} />
            </span>
            <strong>
              {state.category === "text"
                ? t("File exceeds the text preview limit")
                : t("File exceeds the preview limit")}
            </strong>
            <p>{tooLargeMessage}</p>
            <Button
              variant="outline"
              size="sm"
              loading={downloading}
              onClick={() => void handleDownload()}
            >
              <DownloadSimple aria-hidden="true" />
              <span>{t("Download")}</span>
            </Button>
          </div>
        )}

        {state.status === "document" && (
          <DocumentPreview
            kind={state.documentKind}
            sourceKey={`${key}:${attempt}`}
            loadBytes={async (signal) => (await loadBlob(signal)).arrayBuffer()}
          />
        )}

        {state.status === "media" && (
          <section
            className={styles.mediaFrame}
            aria-label={
              state.mediaKind === "audio"
                ? t("Audio preview")
                : state.mediaKind === "video"
                  ? t("Video preview")
                  : t("Image preview")
            }
          >
            {mediaDecodeError ? (
              <p role="alert">
                {t(
                  "This {{kind}} cannot be previewed. The browser may not support its codec. Download the original to inspect it.",
                  { kind: mediaLabel },
                )}
              </p>
            ) : state.mediaKind === "audio" ? (
              <audio
                ref={(element) => {
                  player.current = element;
                }}
                src={state.url}
                controls
                preload="metadata"
                aria-label={t("Audio preview: {{name}}", { name })}
                onError={() => setMediaDecodeError(true)}
              />
            ) : state.mediaKind === "video" ? (
              <video
                ref={(element) => {
                  player.current = element;
                }}
                src={state.url}
                controls
                preload="metadata"
                playsInline
                aria-label={t("Video preview: {{name}}", { name })}
                onError={() => setMediaDecodeError(true)}
              />
            ) : (
              <img
                src={state.url}
                alt={name}
                onError={() => setMediaDecodeError(true)}
              />
            )}
          </section>
        )}

        {state.status === "text" && (
          <>
            {mode === "preview" && state.markdown && (
              <article
                className={styles.markdown}
                aria-label={t("Markdown preview")}
              >
                <MessageText text={state.text} />
              </article>
            )}
            {mode === "preview" && state.svg && (
              <div className={styles.svgCanvas}>
                {/* Data URL in <img> uses isolated SVG image mode; never inject SVG markup into DOM. */}
                <img
                  src={
                    "data:image/svg+xml;charset=utf-8," +
                    encodeURIComponent(state.text)
                  }
                  alt={name}
                />
              </div>
            )}
            {(textOpened || mode === "text") && (
              <div className={styles.editor} hidden={mode !== "text"}>
                <SourceEditor
                  ref={editor}
                  value={state.text}
                  language="plain"
                  filename={name}
                  wrap={wrap}
                  label={t("File text: {{path}}", { path: name })}
                  fill
                  readOnly
                />
              </div>
            )}
          </>
        )}

        {(source || (related && related.length > 0)) && (
          <div className={styles.metadataSection}>
            {source && (
              <details className={styles.disclosure}>
                <summary>{t("Source and revision")}</summary>
                <div className={styles.disclosureBody}>
                  {"path" in source && (
                    <p>
                      {t("Server snapshot - {{path}}", { path: source.path })}
                    </p>
                  )}
                  {"repository_path" in source && (
                    <p>
                      {t("{{comparison}} - {{path}} - HEAD {{head}}", {
                        comparison: source.comparison,
                        path: source.repository_path,
                        head: source.head_oid ?? t("empty tree"),
                      })}
                    </p>
                  )}
                  <pre className={styles.metaCode}>
                    {JSON.stringify(source, null, 2)}
                  </pre>
                </div>
              </details>
            )}
            {related && related.length > 0 && (
              <details className={styles.disclosure}>
                <summary>{t("What the model sees")}</summary>
                <div className={styles.disclosureBody}>
                  {related.map((item, offset) => (
                    <pre key={offset} className={styles.metaCode}>
                      {item.text || JSON.stringify(item.value, null, 2)}
                    </pre>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
      </div>
    </SheetPopup>
  );
}
