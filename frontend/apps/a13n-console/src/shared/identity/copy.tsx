import { Button, Tooltip, TooltipTrigger, TooltipPopup } from "a13n-ui";

import { useEffect, useState, type ComponentProps } from "react";

import { CheckIcon, CopyIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import styles from "./identity.module.css";
import { ErrorToast } from "../feedback";

export function CopyButton({
  value,
  iconOnly = false,
  copyLabel,
  text,
  variant,
}: {
  value: string | (() => string);
  iconOnly?: boolean;
  copyLabel?: string;
  text?: string;
  variant?: ComponentProps<typeof Button>["variant"];
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
  const button = (
    <Button
      className={
        text ? styles.label : iconOnly ? "text-muted-foreground" : undefined
      }
      variant={variant ?? (text || iconOnly ? "ghost" : "outline")}
      size={iconOnly && !text ? "icon-xs" : "sm"}
      aria-label={label}
      disabled={status === "pending"}
      onClick={() => void copy()}
      type="button"
    >
      {text ? (
        <span className="grid place-items-center">
          <span
            className={
              status === "copied"
                ? "invisible col-start-1 row-start-1"
                : "col-start-1 row-start-1"
            }
          >
            {text}
          </span>
          {status === "copied" && (
            <CheckIcon className="col-start-1 row-start-1 size-3" />
          )}
        </span>
      ) : (
        <>
          {status === "copied" ? (
            <CheckIcon className="size-3" />
          ) : (
            <CopyIcon className="size-3" />
          )}
          {iconOnly ? undefined : label}
        </>
      )}
    </Button>
  );
  return (
    <span className={styles.control}>
      {text ? (
        <Tooltip>
          <TooltipTrigger render={button} />
          <TooltipPopup>
            {status === "copied" ? t("Copied") : t("Click to copy")}
          </TooltipPopup>
        </Tooltip>
      ) : (
        button
      )}
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
