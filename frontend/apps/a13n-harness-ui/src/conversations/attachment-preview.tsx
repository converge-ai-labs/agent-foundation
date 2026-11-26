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
  CaretLeft,
  CaretRight,
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
import { useSwipeDismiss } from "../shell/swipe-dismiss";
import { MessageText } from "./message-text";
import {
  getCachedAttachment,
  loadRetainedAttachment,
  prefetchRetainedAttachment,
} from "./attachment-cache";
import {
  classifyAttachmentPreview,
  decodeUtf8Text,
  formatAttachmentSize,
  TEXT_PREVIEW_LIMIT,
} from "./attachment-preview-policy";
import styles from "./attachment-preview.module.css";

export const DOWNLOAD_URL_REVOKE_DELAY_MS = 10_000;

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

export function triggerBlobDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noreferrer";
  anchor.referrerPolicy = "no-referrer";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  // Keep the temporary Object URL alive long enough for the browser to initiate
  // saving the file even if the user immediately closes the drawer.
  setTimeout(() => URL.revokeObjectURL(url), DOWNLOAD_URL_REVOKE_DELAY_MS);
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

export function attachmentTargetIdentity(
  target: AttachmentPreviewTarget,
): string {
  return target.kind === "retained"
    ? `retained:${target.threadId}:${target.attachmentId}`
    : `local:${target.file.name}:${target.file.size}:${target.file.lastModified}`;
}

export function AttachmentPreviewSheet({
  target,
  items,
  onSelect,
  onClose,
}: {
  target: AttachmentPreviewTarget | null;
  items?: readonly AttachmentPreviewTarget[];
  onSelect?: (next: AttachmentPreviewTarget) => void;
  onClose: () => void;
}) {
  // Retain the last non-null target while the Sheet plays its close slide-out animation.
  const [renderedTarget, setRenderedTarget] =
    useState<AttachmentPreviewTarget | null>(target);
  if (target && target !== renderedTarget) {
    setRenderedTarget(target);
  }
  const activeTarget = target ?? renderedTarget;
  const editorActionsRef = useRef<{
    find: () => boolean;
    closeFind: () => boolean;
  } | null>(null);
  const swipeDismiss = useSwipeDismiss(onClose, "right");

  const activeIdentity = activeTarget
    ? attachmentTargetIdentity(activeTarget)
    : undefined;
  const activeIndex =
    items && activeIdentity
      ? items.findIndex(
          (item) => attachmentTargetIdentity(item) === activeIdentity,
        )
      : -1;
  const navigation =
    items && items.length > 1 && activeIndex >= 0 && onSelect
      ? {
          index: activeIndex,
          total: items.length,
          previous:
            activeIndex > 0
              ? () => onSelect(items[activeIndex - 1]!)
              : undefined,
          next:
            activeIndex < items.length - 1
              ? () => onSelect(items[activeIndex + 1]!)
              : undefined,
          neighbors: [items[activeIndex - 1], items[activeIndex + 1]].filter(
            (item): item is AttachmentPreviewTarget => !!item,
          ),
        }
      : undefined;

  return (
    <Sheet
      open={!!target}
      onOpenChange={(open, eventDetails) => {
        if (!open) {
          if (
            eventDetails.reason === "escape-key" &&
            !(
              eventDetails.event?.target instanceof Element &&
              eventDetails.event.target.closest('[role="menu"]')
            ) &&
            editorActionsRef.current?.closeFind()
          ) {
            eventDetails.cancel();
            return;
          }
          onClose();
        }
      }}
      onOpenChangeComplete={(open) => {
        if (!open) setRenderedTarget(null);
      }}
    >
      {activeTarget && (
        <SheetPopup
          ref={swipeDismiss}
          side="right"
          showCloseButton={false}
          className={styles.drawer}
          onKeyDownCapture={(event) => {
            if (event.nativeEvent.isComposing) return;
            if (
              event.key === "Escape" &&
              !(
                event.target instanceof Element &&
                event.target.closest('[role="menu"]')
              ) &&
              editorActionsRef.current?.closeFind()
            ) {
              event.preventDefault();
              event.stopPropagation();
              event.nativeEvent.stopImmediatePropagation?.();
              return;
            }
            if (
              navigation &&
              event.altKey &&
              !event.ctrlKey &&
              !event.metaKey &&
              !event.shiftKey
            ) {
              if (event.key === "ArrowLeft" && navigation.previous) {
                event.preventDefault();
                event.stopPropagation();
                navigation.previous();
                return;
              }
              if (event.key === "ArrowRight" && navigation.next) {
                event.preventDefault();
                event.stopPropagation();
                navigation.next();
                return;
              }
            }
            if (
              !isFindShortcut(event.nativeEvent) ||
              (event.target instanceof Element &&
                event.target.closest(".cm-editor"))
            )
              return;
            if (editorActionsRef.current?.find()) {
              event.preventDefault();
              event.stopPropagation();
            }
          }}
        >
          <AttachmentPreviewBody
            key={attachmentTargetIdentity(activeTarget)}
            target={activeTarget}
            open={!!target}
            editorActionsRef={editorActionsRef}
            navigation={navigation}
          />
        </SheetPopup>
      )}
    </Sheet>
  );
}

type SiblingNavigation = {
  index: number;
  total: number;
  previous?: () => void;
  next?: () => void;
  neighbors: AttachmentPreviewTarget[];
};

function resolveInitialState(
  name: string,
  mediaType: string | undefined,
  size: number | undefined,
  cached: ReturnType<typeof getCachedAttachment>,
): PreviewState {
  const effectiveSize = size ?? cached?.size;
  const effectiveMediaType = mediaType || cached?.blob.type || undefined;
  const plan = classifyAttachmentPreview({
    name,
    mediaType: effectiveMediaType,
    size: effectiveSize,
  });
  if (plan.kind === "unsupported") return { status: "unsupported" };
  if (plan.kind === "too-large")
    return { status: "too-large", category: plan.category };
  if (plan.kind === "document")
    return { status: "document", documentKind: plan.documentKind };
  if (plan.kind === "text" && cached) {
    if (cached.size > TEXT_PREVIEW_LIMIT)
      return { status: "too-large", category: "text" };
    if (cached.text === null) return { status: "binary" };
    if (typeof cached.text === "string") {
      return {
        status: "text",
        text: cached.text,
        markdown: plan.markdown,
        svg: plan.svg || cached.blob.type === "image/svg+xml",
      };
    }
  }
  return { status: "loading" };
}

function AttachmentPreviewBody({
  target,
  open,
  editorActionsRef,
  navigation,
}: {
  target: AttachmentPreviewTarget;
  open: boolean;
  editorActionsRef: React.MutableRefObject<{
    find: () => boolean;
    closeFind: () => boolean;
  } | null>;
  navigation?: SiblingNavigation;
}) {
  const { t, i18n } = useTranslation();
  const transport = useTransport();
  const key = attachmentTargetIdentity(target);
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

  const initialCached =
    target.kind === "retained"
      ? getCachedAttachment(transport, target.threadId, target.attachmentId)
      : undefined;

  const [resolvedSize, setResolvedSize] = useState<number | undefined>(
    initialSize ?? initialCached?.size,
  );
  const [resolvedMediaType, setResolvedMediaType] = useState<
    string | undefined
  >(initialMediaType || initialCached?.blob.type || undefined);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<PreviewState>(() =>
    resolveInitialState(name, initialMediaType, initialSize, initialCached),
  );

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
  const downloadController = useRef<AbortController | null>(null);

  const isText = state.status === "text";
  const hasRichPreview = isText && (state.markdown || state.svg);
  const mode = chosenMode ?? (hasRichPreview ? "preview" : "text");
  const showSourceToolbar = isText && mode === "text";

  useEffect(() => {
    editorActionsRef.current = {
      find: () => {
        if (!showSourceToolbar || !editor.current) return false;
        editor.current.find();
        return true;
      },
      closeFind: () => editor.current?.closeFind() ?? false,
    };
    return () => {
      editorActionsRef.current = null;
    };
  }, [editorActionsRef, showSourceToolbar]);

  useEffect(() => () => downloadController.current?.abort(), []);

  useEffect(() => {
    if (copyState !== "copied") return;
    const timer = setTimeout(() => setCopyState("idle"), 2000);
    return () => clearTimeout(timer);
  }, [copyState]);

  // Quietly warm immediate sibling text attachments so Prev/Next feels instant.
  useEffect(() => {
    if (!open || !navigation?.neighbors.length) return;
    for (const neighbor of navigation.neighbors) {
      if (neighbor.kind === "retained") {
        prefetchRetainedAttachment(transport, neighbor.threadId, {
          attachment_id: neighbor.attachmentId,
          name: neighbor.name,
          media_type: neighbor.mediaType,
          size: neighbor.size,
        });
      }
    }
  }, [open, transport, key, navigation?.neighbors]);

  const loadBlob = async (signal?: AbortSignal): Promise<Blob> => {
    if (target.kind === "local") return target.file;
    const entry = await loadRetainedAttachment(
      transport,
      target.threadId,
      target.attachmentId,
      { signal },
    );
    if (!signal?.aborted) {
      if (initialSize === undefined) setResolvedSize(entry.size);
      if (!initialMediaType && entry.blob.type)
        setResolvedMediaType(entry.blob.type);
    }
    return entry.blob;
  };

  useEffect(() => {
    if (!open) return;
    const cached =
      target.kind === "retained"
        ? getCachedAttachment(transport, target.threadId, target.attachmentId)
        : undefined;
    const plan = classifyAttachmentPreview({
      name,
      mediaType: initialMediaType || cached?.blob.type || undefined,
      size: initialSize ?? cached?.size,
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
    if (plan.kind === "text" && cached) {
      if (cached.size > TEXT_PREVIEW_LIMIT) {
        setState({ status: "too-large", category: "text" });
        return;
      }
      if (cached.text === null) {
        setState({ status: "binary" });
        return;
      }
      if (typeof cached.text === "string") {
        setState({
          status: "text",
          text: cached.text,
          markdown: plan.markdown,
          svg: plan.svg || cached.blob.type === "image/svg+xml",
        });
        return;
      }
    }

    const controller = new AbortController();
    let mediaUrl: string | undefined;
    setState({ status: "loading" });
    setMediaDecodeError(false);

    (async () => {
      if (target.kind === "retained") {
        const entry = await loadRetainedAttachment(
          transport,
          target.threadId,
          target.attachmentId,
          {
            decodeText: plan.kind === "text",
            signal: controller.signal,
          },
        );
        if (controller.signal.aborted) return;
        if (initialSize === undefined) setResolvedSize(entry.size);
        if (!initialMediaType && entry.blob.type)
          setResolvedMediaType(entry.blob.type);

        const effectiveMedia =
          plan.kind === "media"
            ? plan.mediaKind
            : !initialMediaType && !/\.ts$/i.test(name)
              ? mediaKind(entry.blob.type)
              : null;
        if (effectiveMedia) {
          if (entry.size > MAX_MEDIA_BYTES) {
            setState({ status: "too-large", category: effectiveMedia });
            return;
          }
          mediaUrl = URL.createObjectURL(entry.blob);
          setState({
            status: "media",
            mediaKind: effectiveMedia,
            url: mediaUrl,
          });
          return;
        }
        if (entry.size > TEXT_PREVIEW_LIMIT) {
          setState({ status: "too-large", category: "text" });
          return;
        }
        const decoded =
          entry.text !== undefined
            ? entry.text
            : decodeUtf8Text(await entry.blob.arrayBuffer());
        if (controller.signal.aborted) return;
        if (decoded === null) {
          setState({ status: "binary" });
          return;
        }
        setState({
          status: "text",
          text: decoded,
          markdown: plan.kind === "text" && plan.markdown,
          svg:
            (plan.kind === "text" && plan.svg) ||
            entry.blob.type === "image/svg+xml",
        });
        return;
      }

      const blob = target.file;
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
  }, [open, transport, key, name, initialMediaType, initialSize, attempt]);

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
      triggerBlobDownload(blob, name);
    } catch (failure) {
      if (!controller.signal.aborted) setDownloadError(failure);
    } finally {
      if (!controller.signal.aborted) setDownloading(false);
    }
  };

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
    <>
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
        <div className={styles.headerControls}>
          {navigation && (
            <div className={styles.navSwitcher}>
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={t("Previous attachment")}
                title={t("Previous attachment (Alt+Left)")}
                disabled={!navigation.previous}
                onClick={navigation.previous}
              >
                <CaretLeft />
              </Button>
              <span
                className={styles.navCount}
                aria-label={t("Attachment {{index}} of {{total}}", {
                  index: navigation.index + 1,
                  total: navigation.total,
                })}
              >
                {navigation.index + 1} / {navigation.total}
              </span>
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={t("Next attachment")}
                title={t("Next attachment (Alt+Right)")}
                disabled={!navigation.next}
                onClick={navigation.next}
              >
                <CaretRight />
              </Button>
            </div>
          )}
          <SheetClose
            aria-label={t("Close")}
            className={styles.closeButton}
            render={<Button size="icon-sm" variant="ghost" />}
          >
            <X />
          </SheetClose>
        </div>
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
    </>
  );
}
