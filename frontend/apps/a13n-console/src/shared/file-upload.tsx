import { Button, Input } from "a13n-ui";
import { FileArrowUpIcon, XIcon } from "@phosphor-icons/react";
import { useRef } from "react";
import { useTranslation } from "react-i18next";

export function FileUpload({
  label,
  file,
  onSelect,
  acceptedFileTypes,
  disabled,
}: {
  label: string;
  file?: File;
  onSelect: (file: File | undefined) => void;
  acceptedFileTypes?: string[];
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const input = useRef<HTMLInputElement>(null);
  return (
    <div
      className="flex min-w-0 flex-col gap-3 rounded-xl border border-dashed bg-card p-4"
      role="group"
      aria-label={label}
    >
      <span className="text-sm font-medium">{label}</span>
      <div className="flex flex-wrap items-center gap-3">
        <Input
          ref={input}
          type="file"
          nativeInput
          unstyled
          hidden
          accept={acceptedFileTypes?.join(",")}
          disabled={disabled}
          onChange={(event) => {
            const selected = event.target.files?.[0];
            if (selected) onSelect(selected);
            event.target.value = "";
          }}
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled}
          onClick={() => input.current?.click()}
        >
          <FileArrowUpIcon aria-hidden="true" />
          {t("Choose file")}
        </Button>
        {file && (
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
        )}
      </div>
      <p className="break-all text-xs text-muted-foreground" aria-live="polite">
        {file?.name ?? t("No file selected")}
      </p>
    </div>
  );
}
