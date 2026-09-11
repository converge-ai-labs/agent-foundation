import { Button } from "a13n-ui";

import { useMutation } from "@tanstack/react-query";
import { useEffect } from "react";

import { CheckIcon, CopyIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import styles from "./copy.module.css";

export function CopyButton({
  value,
  iconOnly = false,
  copyLabel,
}: {
  value: string;
  iconOnly?: boolean;
  copyLabel?: string;
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
  const label = copy.isSuccess
    ? t("Copied")
    : (copyLabel ?? t(iconOnly ? "Copy ID" : "Copy"));
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

export function Identifier({
  value,
  primary = false,
}: {
  value: string;
  primary?: boolean;
}) {
  return (
    <span
      className={styles.identifierText}
      data-primary={primary || undefined}
      title={value}
    >
      {value}
    </span>
  );
}

export function CopyableId({
  value,
  primary = false,
}: {
  value: string;
  primary?: boolean;
}) {
  return (
    <span className={styles.identifier}>
      <Identifier value={value} primary={primary} />
      <CopyButton key={value} value={value} iconOnly />
    </span>
  );
}

export function CopyableResourceKey({ value }: { value: string }) {
  const { t } = useTranslation();
  return (
    <span className="relative z-1 flex min-w-0 items-center gap-1 text-muted-foreground">
      <code className="max-w-[min(360px,35vw)] truncate text-xs" title={value}>
        {value}
      </code>
      <CopyButton value={value} iconOnly copyLabel={t("Copy resource key")} />
    </span>
  );
}
