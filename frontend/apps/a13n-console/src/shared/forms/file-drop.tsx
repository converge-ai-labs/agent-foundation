import { Button } from "a13n-ui";
import { FileIcon, UploadSimpleIcon, XIcon } from "@phosphor-icons/react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { IconTile } from "../identity";
import styles from "./forms.module.css";

/**
 * The one dashed outline in the product: it says "drop a file here" and
 * nothing else does. The label names the group for assistive technology; the
 * hint is what a sighted reader sees.
 */
export function FileDrop({
  label,
  hint,
  accept,
  file,
  disabled,
  onSelect,
}: {
  label: string;
  hint: string;
  accept?: string[];
  file?: File;
  disabled?: boolean;
  onSelect: (file: File | undefined) => void;
}) {
  const { t } = useTranslation();
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const choose = (
    <Button
      type="button"
      variant="outline"
      size="sm"
      disabled={disabled}
      onClick={() => input.current?.click()}
    >
      {file ? t("Replace file") : t("Choose file")}
    </Button>
  );
  return (
    <div
      className={styles.dropZone}
      data-dragging={dragging}
      role="group"
      aria-label={label}
      onDragOver={(event) => {
        event.preventDefault();
        if (disabled) return;
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        if (disabled) return;
        setDragging(false);
        const dropped = event.dataTransfer.files[0];
        if (dropped) onSelect(dropped);
      }}
    >
      <input
        ref={input}
        type="file"
        hidden
        accept={accept?.join(",")}
        disabled={disabled}
        aria-label={label}
        onChange={(event) => {
          const selected = event.target.files?.[0];
          if (selected) onSelect(selected);
          event.target.value = "";
        }}
      />
      {file ? (
        <>
          <span className={styles.dropZoneFile}>
            <FileIcon size={16} aria-hidden="true" />
            {file.name}
          </span>
          <div className={styles.dropZoneActions}>
            {choose}
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              disabled={disabled}
              aria-label={t("Remove file")}
              onClick={() => onSelect(undefined)}
            >
              <XIcon aria-hidden="true" />
            </Button>
          </div>
        </>
      ) : (
        <>
          <IconTile size={36} tone="elevated">
            <UploadSimpleIcon size={16} aria-hidden="true" />
          </IconTile>
          <p>{hint}</p>
          {choose}
        </>
      )}
    </div>
  );
}
