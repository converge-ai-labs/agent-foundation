import { useEffect, useState } from "react";
import { Button, Dialog, DialogPopup, DialogTitle } from "a13n-ui";
import {
  ArrowsOut,
  MagnifyingGlassPlus,
  DownloadSimple,
} from "@phosphor-icons/react";
import type { Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import { attachmentPath } from "./attachment-thumbnail";
import styles from "./image-preview.module.css";

export function ImagePreview({
  src,
  name,
  close,
  loading = false,
  error,
  retry,
}: {
  src?: string;
  name: string;
  close: () => void;
  loading?: boolean;
  error?: unknown;
  retry?: () => void;
}) {
  const [actualSize, setActualSize] = useState(false);
  const [imageError, setImageError] = useState(false);
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <DialogPopup
        className={styles.viewer}
        closeProps={{ "aria-label": "Close image preview" }}
      >
        <header className={styles.header}>
          <DialogTitle className={styles.title}>{name}</DialogTitle>
          <div className={styles.actions}>
            <Button
              variant="ghost"
              size="sm"
              disabled={!src || imageError}
              onClick={() => setActualSize(!actualSize)}
            >
              {actualSize ? <ArrowsOut /> : <MagnifyingGlassPlus />}
              {actualSize ? "Fit to screen" : "Actual size"}
            </Button>
            {src && (
              <a href={src} download={name} aria-label="Download image">
                <DownloadSimple />
                Download
              </a>
            )}
          </div>
        </header>
        <div className={styles.canvas} data-actual-size={actualSize}>
          {loading && <p role="status">Loading image…</p>}
          <ErrorNotice error={error} retry={retry} />
          {imageError && (
            <p role="alert">
              This image cannot be previewed. Download the original to inspect
              it.
            </p>
          )}
          {src && !imageError && (
            <button
              type="button"
              className={styles.imageButton}
              aria-label={
                actualSize ? "Fit image to screen" : "Zoom image to actual size"
              }
              onClick={() => setActualSize(!actualSize)}
            >
              <img src={src} alt={name} onError={() => setImageError(true)} />
            </button>
          )}
        </div>
      </DialogPopup>
    </Dialog>
  );
}

export function RetainedImagePreview({
  threadId,
  attachment,
  close,
}: {
  threadId: string;
  attachment: Schema<"ThreadAttachment">;
  close: () => void;
}) {
  const transport = useTransport();
  const [src, setSrc] = useState<string>();
  const [error, setError] = useState<unknown>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let url: string | undefined;
    setSrc(undefined);
    setError(undefined);
    void transport
      .fetch(attachmentPath(threadId, attachment.attachment_id), {
        signal: controller.signal,
      })
      .then((response) => response.blob())
      .then((blob) => {
        if (controller.signal.aborted) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted) setError(failure);
      });
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [transport, threadId, attachment.attachment_id, attempt]);
  return (
    <ImagePreview
      src={src}
      name={attachment.name}
      close={close}
      loading={!src && !error}
      error={error}
      retry={() => setAttempt((value) => value + 1)}
    />
  );
}
