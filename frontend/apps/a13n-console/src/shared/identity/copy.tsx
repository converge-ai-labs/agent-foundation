import { Button } from "a13n-ui";

import { useEffect, useState } from "react";

import { CheckIcon, CopyIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import styles from "./identity.module.css";
import { ErrorToast } from "../feedback";

export function CopyButton({
  value,
  iconOnly = false,
  copyLabel,
}: {
  value: string | (() => string);
  iconOnly?: boolean;
  copyLabel?: string;
}) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<
    "idle" | "pending" | "copied" | "failed"
  >("idle");
  async function copy() {
    setStatus("pending");
    try {
      await navigator.clipboard.writeText(
        typeof value === "function" ? value() : value,
      );
      setStatus("copied");
    } catch {
      setStatus("failed");
    }
  }
  useEffect(() => {
    if (status !== "copied") return;
    const timer = window.setTimeout(() => setStatus("idle"), 1500);
    return () => window.clearTimeout(timer);
  }, [status]);
  const label =
    status === "copied"
      ? t("Copied")
      : (copyLabel ?? t(iconOnly ? "Copy ID" : "Copy"));
  return (
    <span className={styles.control}>
      <Button
        className={iconOnly ? "text-muted-foreground" : undefined}
        variant={iconOnly ? "ghost" : "outline"}
        size={iconOnly ? "icon-xs" : "sm"}
        aria-label={label}
        disabled={status === "pending"}
        onClick={() => void copy()}
        type="button"
      >
        {status === "copied" ? (
          <CheckIcon className="size-3" />
        ) : (
          <CopyIcon className="size-3" />
        )}
        {iconOnly ? undefined : label}
      </Button>
      <span role="status" className="visually-hidden">
        {status === "copied" ? t("Copied") : ""}
      </span>
      <ErrorToast
        error={status === "failed" ? new Error(t("Copy failed.")) : undefined}
      />
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
