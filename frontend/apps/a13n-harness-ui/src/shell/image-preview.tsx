import { useTranslation } from "../i18n";
import { useState } from "react";
import { Button, Dialog, DialogClose, DialogPopup, DialogTitle } from "a13n-ui";
import {
  ArrowsOut,
  MagnifyingGlassPlus,
  DownloadSimple,
  X,
} from "@phosphor-icons/react";
import { ErrorNotice } from "./ui";
import styles from "./image-preview.module.css";

export function ImagePreview({
  src,
  name,
  close,
  actions,
  loading = false,
  error,
  retry,
}: {
  src?: string;
  name: string;
  close: () => void;
  actions?: import("react").ReactNode;
  loading?: boolean;
  error?: unknown;
  retry?: () => void;
}) {
  const { t } = useTranslation();
  const [actualSize, setActualSize] = useState(false);
  const [failedSource, setFailedSource] = useState<string>();
  const imageError = !!src && failedSource === src;
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <DialogPopup className={styles.viewer} showCloseButton={false}>
        <header className={styles.header}>
          <DialogTitle className={styles.title}>{name}</DialogTitle>
          <div className={styles.actions}>
            {actions}
            <Button
              variant="ghost"
              size="sm"
              aria-label={actualSize ? t("Fit to screen") : t("Actual size")}
              disabled={!src || imageError}
              onClick={() => setActualSize(!actualSize)}
            >
              {actualSize ? <ArrowsOut /> : <MagnifyingGlassPlus />}
              <span className={styles.actionLabel}>
                {actualSize ? t("Fit to screen") : t("Actual size")}
              </span>
            </Button>
            {src && (
              <a href={src} download={name} aria-label={t("Download image")}>
                <DownloadSimple />
                <span className={styles.actionLabel}>{t("Download")}</span>
              </a>
            )}
            <DialogClose
              aria-label={t("Close image preview")}
              render={<Button size="icon-sm" variant="ghost" />}
            >
              <X />
            </DialogClose>
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
