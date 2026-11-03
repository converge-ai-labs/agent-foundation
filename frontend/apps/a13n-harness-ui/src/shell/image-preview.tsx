import { useTranslation } from "../i18n";
import { useState } from "react";
import { useTranslation } from "../i18n";
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
  const { t } = useTranslation();
  const [actualSize, setActualSize] = useState(false);
  const [failedSource, setFailedSource] = useState<string>();
  const { t } = useTranslation();
  const imageError = !!src && failedSource === src;
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <DialogPopup
        className={styles.viewer}
        closeProps={{ "aria-label": t("Close image preview") }}
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
              {actualSize ? t("Fit to screen") : t("Actual size")}
            </Button>
            {src && (
              <a href={src} download={name} aria-label={t("Download image")}>
                <DownloadSimple />
                {t("Download")}
              </a>
            )}
          </div>
        </header>
        <div className={styles.canvas} data-actual-size={actualSize}>
          {loading && <p role="status">{t("Loading image…")}</p>}
          <ErrorNotice error={error} retry={retry} />
          {imageError && (
            <p role="alert">
              {t(
                "This image cannot be previewed. Download the original to inspect it.",
              )}
            </p>
          )}
          {src && !imageError && (
            <button
              type="button"
              className={styles.imageButton}
              aria-label={
                actualSize
                  ? t("Fit image to screen")
                  : t("Zoom image to actual size")
              }
              onClick={() => setActualSize(!actualSize)}
            >
              <img src={src} alt={name} onError={() => setFailedSource(src)} />
            </button>
          )}
        </div>
      </DialogPopup>
    </Dialog>
  );
}
