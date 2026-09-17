import { useEffect, useState } from "react";
import { Button, Tooltip, TooltipPopup, TooltipTrigger } from "a13n-ui";
import { Check, Copy } from "@phosphor-icons/react";
import styles from "./conversation.module.css";

export function CopyMessage({
  text,
  truncated = false,
}: {
  text: string;
  truncated?: boolean;
}) {
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
      ? "Copied"
      : truncated
        ? "Copy available preview"
        : "Copy message";
  return (
    <div className={styles.messageActions}>
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
              await navigator.clipboard.writeText(text);
              setState("copied");
            } catch {
              setState("error");
            }
          }}
        >
          {state === "copied" ? (
            <Check aria-hidden="true" />
          ) : (
            <Copy aria-hidden="true" />
          )}
        </TooltipTrigger>
        <TooltipPopup>{label}</TooltipPopup>
      </Tooltip>
      <span className="sr-only" aria-live="polite">
        {state === "copied" ? "Copied to clipboard" : ""}
      </span>
      {state === "error" && (
        <small className={styles.copyError} role="alert">
          Could not copy. Try again or select the text to copy manually.
        </small>
      )}
    </div>
  );
}
