import { Button } from "a13n-ui";

import { useMutation } from "@tanstack/react-query";
import { useEffect } from "react";

import { CheckIcon, CopyIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import styles from "./copy.module.css";

export function CopyButton({
  value,
  iconOnly = false,
}: {
  value: string;
  iconOnly?: boolean;
}) {
  const { t } = useTranslation();
  const copy = useMutation({
    mutationFn: () => navigator.clipboard.writeText(value),
  });
  useEffect(() => {
    if (!copy.isSuccess) return;
    const timer = window.setTimeout(() => copy.reset(), 1500);
    return () => window.clearTimeout(timer);
  }, [copy.isSuccess, copy.reset]);
  const label = t(copy.isSuccess ? "Copied" : iconOnly ? "Copy ID" : "Copy");
  return (
    <span className={styles.control}>
      <Button
        className={iconOnly ? "text-muted-foreground" : undefined}
        variant={iconOnly ? "ghost" : "outline"}
        size={iconOnly ? "icon-xs" : "sm"}
        aria-label={label}
        disabled={copy.isPending}
        onClick={() => copy.mutate()}
        type="button"
      >
        {copy.isSuccess ? (
          <CheckIcon className="size-3" />
        ) : (
          <CopyIcon className="size-3" />
        )}
        {iconOnly ? undefined : label}
      </Button>
      <span role="status" className="visually-hidden">
        {copy.isSuccess ? t("Copied") : ""}
      </span>
      {copy.isError && (
        <span className={styles.error} role="alert">
          {t("Copy failed. Try again.")}
        </span>
      )}
    </span>
  );
}

export function CopyableId({ value }: { value: string }) {
  return (
    <span className={styles.identifier}>
      <code>{value}</code>
      <CopyButton key={value} value={value} iconOnly />
    </span>
  );
}
