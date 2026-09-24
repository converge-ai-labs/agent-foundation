import { useState } from "react";
import { Button, Dialog, DialogPopup, DialogTitle } from "a13n-ui";
import {
  ArrowsOut,
  MagnifyingGlassPlus,
  DownloadSimple,
} from "@phosphor-icons/react";
import { ErrorNotice } from "./ui";
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
