import { ArrowLeftIcon, XIcon } from "@phosphor-icons/react";
import { Button, Tooltip, TooltipPopup, TooltipTrigger } from "a13n-ui";
import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";
import styles from "./files.module.css";

export function FileSize({ size }: { size: number }) {
  const { t, i18n } = useTranslation();
  const formatter = new Intl.NumberFormat(i18n?.language, {
    maximumFractionDigits: 1,
  });
  return (
    <small className={styles.metadata}>
      {size >= 1024
        ? t("{{size}} KiB", { size: formatter.format(size / 1024) })
        : t("{{size}} B", { size: formatter.format(size) })}
    </small>
  );
}

export function FileToolbarButton({
  label,
  children,
  ...button
}: ComponentProps<typeof Button> & { label: string }) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            {...button}
            size="icon-sm"
            variant="ghost"
            aria-label={label}
          />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipPopup>{label}</TooltipPopup>
    </Tooltip>
  );
}

export function BackToFiles({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <FileToolbarButton label={t("Back to files")} onClick={onClick}>
      <ArrowLeftIcon size={16} aria-hidden="true" />
    </FileToolbarButton>
  );
}

export function CloseFiles({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <FileToolbarButton label={t("Close files panel")} onClick={onClick}>
      <XIcon size={16} aria-hidden="true" />
    </FileToolbarButton>
  );
}
