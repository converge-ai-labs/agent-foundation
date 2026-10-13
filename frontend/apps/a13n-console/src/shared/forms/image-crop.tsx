import { Button, ModalFrame } from "a13n-ui";
import { useEffect, useId, useState, type RefObject } from "react";
import Cropper from "react-easy-crop";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../feedback";
import { cropImage, loadImage, type ImageCrop } from "./image";
import styles from "./image-crop.module.css";

export function ImageCropDialog({
  url,
  finalFocus,
  onChange,
  close,
}: {
  url: string;
  finalFocus: RefObject<HTMLElement | null>;
  onChange: (file: File) => void | Promise<void>;
  close: () => void;
}) {
  const { t } = useTranslation();
  const zoomId = useId();
  const [image, setImage] = useState<HTMLImageElement>();
  const [crop, setCrop] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [pixels, setPixels] = useState<ImageCrop>();
  const [area, setArea] = useState<ImageCrop>();
  const [error, setError] = useState<unknown>();
  const [loadError, setLoadError] = useState<string>();
  const [processing, setProcessing] = useState(false);
  useEffect(() => {
    let active = true;
    void loadImage(url).then(
      (loaded) => {
        if (active) setImage(loaded);
      },
      (failure: Error) => {
        if (active) setLoadError(failure.message);
      },
    );
    return () => {
      active = false;
    };
  }, [url]);
  const save = async () => {
    if (!image || !pixels || processing) return;
    setProcessing(true);
    setError(undefined);
    try {
      let file: File;
      try {
        file = await cropImage(image, pixels);
      } catch (failure) {
        throw new Error(t((failure as Error).message));
      }
      await onChange(file);
      close();
    } catch (failure) {
      setError(failure);
    } finally {
      setProcessing(false);
    }
  };
  return (
    <ModalFrame
      open
      title={t("Crop image")}
      description={t(
        "Drag to position your image. Use the arrow keys to move it.",
      )}
      closeLabel={t("Close")}
      finalFocus={finalFocus}
      onOpenChange={(open, details) => {
        if (!open) {
          if (processing) details.cancel();
          else close();
        }
      }}
      footer={
        <>
          <Button
            type="button"
            variant="outline"
            disabled={processing}
            onClick={close}
          >
            {t("Cancel")}
          </Button>
          <Button
            type="button"
            loading={processing}
            disabled={!image || !pixels}
            onClick={() => void save()}
          >
            {t("Use image")}
          </Button>
        </>
      }
    >
      <div className={styles.editor}>
        <div className={styles.stage}>
          {image ? (
            <Cropper
              image={url}
              crop={crop}
              zoom={zoom}
              aspect={1}
              minZoom={1}
              maxZoom={3}
              onCropChange={(position) => {
                if (!processing) setCrop(position);
              }}
              onZoomChange={(value) => {
                if (!processing) setZoom(value);
              }}
              cropperProps={{
                "aria-label": t("Crop position"),
                "aria-disabled": processing,
              }}
              onCropAreaChange={(area, pixels) => {
                setArea(area);
                setPixels(pixels);
              }}
              showGrid={false}
              keyboardStep={10}
              style={{
                containerStyle: {
                  pointerEvents: processing ? "none" : undefined,
                },
              }}
            />
          ) : (
            <p role="status" className={styles.loading}>
              {loadError ? t("Image unavailable") : t("Opening image…")}
            </p>
          )}
        </div>
        <div className={styles.controls}>
          <div className={styles.zoom}>
            <label htmlFor={zoomId}>{t("Zoom")}</label>
            <input
              id={zoomId}
              type="range"
              min="1"
              max="3"
              step="0.01"
              value={zoom}
              disabled={!image || processing}
              onChange={(event) => setZoom(Number(event.target.value))}
            />
          </div>
          <div className={styles.previewGroup}>
            <span>{t("Preview")}</span>
            <div className={styles.preview}>
              {area && (
                <img
                  src={url}
                  alt={t("Cropped image preview")}
                  style={{
                    width: `${10000 / area.width}%`,
                    transform: `translate(-${area.x}%, -${area.y}%)`,
                  }}
                />
              )}
            </div>
          </div>
        </div>
        <p className={styles.hint}>
          {t("Your image is cropped to a square and compressed automatically.")}
        </p>
        {processing && <p role="status">{t("Preparing and saving image…")}</p>}
        <ErrorNotice error={loadError ? new Error(t(loadError)) : error} />
      </div>
    </ModalFrame>
  );
}
