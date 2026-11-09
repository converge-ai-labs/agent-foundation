import { useTranslation } from "../i18n";
import { MessageText } from "./message-text";
import type { ContextState } from "./stream";
import styles from "./conversation.module.css";

function preview(text: string) {
  return text
    .replace(/^#{1,6}\s+(?:context summary|summary|compact summary)\s*$/gim, "")
    .replace(/!?(?:\[([^\]]*)\])\([^)]*\)/g, "$1")
    .replace(/^\s*(?:#{1,6}\s+|>\s*|[-*+]\s+|\d+\.\s+)/gm, "")
    .replace(/```[^\n]*\n?/g, "")
    .replace(/[*_`~]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 240);
}

export function ContextActivity({
  context,
  text,
  state,
  diagnostics,
}: {
  context: "handoff" | "compaction";
  text: string;
  state?: ContextState;
  diagnostics?: string[];
}) {
  const { t } = useTranslation();
  // The reducer reports the operation state; backend diagnostics travel beside
  // it and keep their original values.
  const states: Record<ContextState, string> = {
    "summary-ready": t("Summary ready"),
    prepared: t("Prepared"),
    completed: t("Completed"),
    failed: t("Failed"),
    "in-progress": t("In progress"),
  };
  return (
    <details className={styles.contextActivity} data-kind={context}>
      <summary>
        <span>
          {context === "handoff" ? t("Summary") : t("Compact Summary")}
        </span>
        <span className={styles.contextStatus}>
          {state
            ? [states[state], ...(diagnostics ?? [])].join(" - ")
            : text
              ? t("Saved")
              : t("In progress")}
        </span>
        {text && <span className={styles.contextPreview}>{preview(text)}</span>}
      </summary>
      {text && <MessageText text={text} />}
    </details>
  );
}
