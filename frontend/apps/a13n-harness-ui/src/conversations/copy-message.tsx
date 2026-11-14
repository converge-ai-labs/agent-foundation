import { useTranslation } from "../i18n";
import { useEffect, useState, type ReactNode } from "react";
import { Button, Tooltip, TooltipPopup, TooltipTrigger } from "a13n-ui";
import { Check, Copy } from "@phosphor-icons/react";
import styles from "./conversation.module.css";
import { copyText } from "../platform";

function MessageTime({ timestamp }: { timestamp: string | number }) {
  const { i18n } = useTranslation();
  const date = new Date(timestamp);
  if (!Number.isFinite(date.getTime())) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const weekStart = new Date(today);
  weekStart.setDate(today.getDate() - 6);
  const day: Intl.DateTimeFormatOptions =
    date >= today
      ? {}
      : date >= weekStart
        ? { weekday: "long" }
        : {
            month: "short",
            day: "numeric",
            ...(date.getFullYear() !== today.getFullYear() && {
              year: "numeric",
            }),
          };
  return (
    <time
      className={styles.messageTime}
      dateTime={date.toISOString()}
      title={date.toLocaleString(i18n.language)}
    >
      {date.toLocaleString(i18n.language, {
        ...day,
        hour: "numeric",
        minute: "2-digit",
      })}
    </time>
  );
}

export function CopyMessage({
  text,
  timestamp,
  truncated = false,
  action,
}: {
  text?: string;
  timestamp?: string | number;
  truncated?: boolean;
  action?: ReactNode;
}) {
  const { t } = useTranslation();
  const [state, setState] = useState<"idle" | "copying" | "copied" | "error">(
    "idle",
  );
  useEffect(() => {
    if (state !== "copied") return;
    const timer = setTimeout(() => setState("idle"), 2000);
    return () => clearTimeout(timer);
  }, [state]);
  const label =
    state === "copied"
      ? t("Copied")
      : truncated
        ? t("Copy available preview")
        : t("Copy message");
  return (
    <div className={styles.messageActions}>
      {text?.trim() && (
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="ghost"
                size="icon-sm"
                disabled={state === "copying"}
              />
            }
            className={styles.copyMessage}
            aria-label={label}
            aria-busy={state === "copying" || undefined}
            onClick={async () => {
              setState("copying");
              try {
                await copyText(text);
                setState("copied");
              } catch {
                setState("error");
              }
            }}
          >
            {state === "copied" ? (
              <Check aria-hidden="true" data-copied />
            ) : (
              <Copy aria-hidden="true" />
            )}
          </TooltipTrigger>
          <TooltipPopup>{label}</TooltipPopup>
        </Tooltip>
      )}
      {action}
      {timestamp != null && <MessageTime timestamp={timestamp} />}
      <span className="sr-only" aria-live="polite">
        {state === "copied" ? t("Copied to clipboard") : ""}
      </span>
      {state === "error" && (
        <small className={styles.copyError} role="alert">
          {t("Could not copy. Try again or select the text to copy manually.")}
        </small>
      )}
    </div>
  );
}
