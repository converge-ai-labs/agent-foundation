import {
  Button,
  FormField,
  Input,
  ModalFrame,
  SegmentedControl,
} from "a13n-ui";
import { ImageIcon, PaperclipIcon } from "@phosphor-icons/react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../../layout/workspace";
import type { Schema } from "../../../shared/api";
import { FileDrop, TextAreaField } from "../../../shared/forms";
import styles from "./composer.module.css";

type Mode = "file" | "url" | "structured";

/** Three ways to add content, one field area: the choice is a segment, not a form. */
export function AttachDialog({
  disabled,
  selectionDisabled,
  imageTypes,
  imageLimit,
  error,
  structured,
  onStructuredChange,
  onAttach,
  onUpload,
  uploading,
  uploadedFile,
}: {
  disabled: boolean;
  selectionDisabled: boolean;
  imageTypes?: readonly string[];
  imageLimit: number;
  error: string;
  structured: string;
  onStructuredChange: (value: string) => void;
  onAttach: (attachment: { part: Schema["UrlPart"]; label: string }) => void;
  onUpload: (file: File | undefined) => void;
  uploading: boolean;
  uploadedFile?: File;
}) {
  const { t } = useTranslation();
  const { can } = useWorkspace();
  const canUpload = can("run");
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<Mode>(canUpload ? "file" : "url");
  const [url, setUrl] = useState("");
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      trigger={
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          disabled={disabled}
          aria-label={imageTypes ? t("Attach image") : t("Attach file or URL")}
        >
          {imageTypes ? (
            <ImageIcon size={16} aria-hidden="true" />
          ) : (
            <PaperclipIcon size={16} aria-hidden="true" />
          )}
        </Button>
      }
      size="md"
      title={imageTypes ? t("Attach image") : t("Attach content")}
      description={
        imageTypes
          ? t(
              "Image limit per message: {{limit}}. Each image can be at most 4 MiB.",
              { limit: imageLimit },
            )
          : t("Add a file, a URL, or structured input to your message.")
      }
      closeLabel={t("Close")}
      footer={
        <Button type="button" onClick={() => setOpen(false)}>
          {t("Done")}
        </Button>
      }
    >
      <fieldset
        disabled={disabled}
        className={`${styles.attach} fieldset-reset`}
      >
        {!imageTypes && (
          <SegmentedControl
            label={t("Attachment type")}
            className={styles.segments}
            value={mode}
            onValueChange={(value) => setMode(value as Mode)}
            options={[
              ...(canUpload ? [{ value: "file", label: t("File") }] : []),
              { value: "url", label: t("URL") },
              { value: "structured", label: t("Structured JSON") },
            ]}
          />
        )}
        {(imageTypes || mode === "file") && canUpload && (
          <FileDrop
            label={imageTypes ? t("Upload an image") : t("Upload a file")}
            accept={imageTypes ? [...imageTypes] : undefined}
            hint={
              imageTypes
                ? t(
                    "Drop an image here or choose one to upload ({{formats}}).",
                    {
                      formats: imageTypes
                        .map((type) =>
                          type.slice("image/".length).toUpperCase(),
                        )
                        .join(", "),
                    },
                  )
                : t("Drop a file here or choose one to upload.")
            }
            file={uploadedFile}
            disabled={disabled || uploading || selectionDisabled}
            onSelect={onUpload}
          />
        )}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        {!imageTypes && mode === "url" && (
          <div className={styles.attachRow}>
            <FormField className="min-w-0 flex-1" label={t("File URL")}>
              <Input
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                placeholder="https://"
              />
            </FormField>
            <Button
              type="button"
              variant="outline"
              disabled={!/^https?:\/\//i.test(url)}
              onClick={() => {
                onAttach({ part: { type: "url", url }, label: url });
                setUrl("");
              }}
            >
              {t("Attach URL")}
            </Button>
          </div>
        )}
        {!imageTypes && mode === "structured" && (
          <TextAreaField
            label={t("Structured input (JSON)")}
            value={structured}
            onChange={onStructuredChange}
            code
          />
        )}
      </fieldset>
    </ModalFrame>
  );
}
