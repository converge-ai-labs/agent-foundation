import {
  Button,
  FormField,
  Input,
  ModalFrame,
  SegmentedControl,
} from "a13n-ui";
import { PaperclipIcon } from "@phosphor-icons/react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../../layout/workspace";
import type { Schema } from "../../../shared/api";
import { FileUpload, TextAreaField } from "../../../shared/forms";
import styles from "./composer.module.css";

type Mode = "file" | "url" | "structured";

/** Three ways to add content, one field area: the choice is a segment, not a form. */
export function AttachDialog({
  disabled,
  structured,
  onStructuredChange,
  onAttach,
  onUpload,
  uploading,
  uploadedFile,
}: {
  disabled: boolean;
  structured: string;
  onStructuredChange: (value: string) => void;
  onAttach: (attachment: Schema["BinaryContent"]) => void;
  onUpload: (file: File | undefined) => void;
  uploading: boolean;
  uploadedFile?: File;
}) {
  const { t } = useTranslation();
  const { can } = useWorkspace();
  const canUpload = can("asset.create");
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
          aria-label={t("Attach file or URL")}
          title={t("Attach file or URL")}
        >
          <PaperclipIcon size={16} />
        </Button>
      }
      size="md"
      title={t("Attach content")}
      description={t("Add a file, a URL, or structured input to your message.")}
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
        {mode === "file" && canUpload && (
          <FileUpload
            label={t("Upload a file")}
            file={uploadedFile}
            disabled={disabled || uploading}
            onSelect={onUpload}
          />
        )}
        {mode === "url" && (
          <div className={styles.attachRow}>
            <FormField className="w-full min-w-0" label={t("File URL")}>
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
                onAttach({ type: "binary", source: { type: "url", url } });
                setUrl("");
              }}
            >
              {t("Attach URL")}
            </Button>
          </div>
        )}
        {mode === "structured" && (
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
