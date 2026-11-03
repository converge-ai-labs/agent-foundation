import { useTranslation } from "../i18n";
import { MessageText } from "./message-text";
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
  status,
}: {
  context: "handoff" | "compaction";
  text: string;
  status?: string;
}) {
  const { t } = useTranslation();
  // The stream reducer joins a known phase with backend diagnostics; only the
  // leading phase is application-owned text.
  const phases: Record<string, string> = {
    "Summary ready": t("Summary ready"),
    Prepared: t("Prepared"),
    Completed: t("Completed"),
    Failed: t("Failed"),
    "In progress": t("In progress"),
  };
  const [phase, ...diagnostics] = (status ?? "").split(" · ");
  return (
    <details className={styles.contextActivity} data-kind={context}>
      <summary>
        <span>
          {context === "handoff" ? t("Summary") : t("Compact Summary")}
        </span>
        <span className={styles.contextStatus}>
          {status
            ? [phases[phase] ?? phase, ...diagnostics].join(" · ")
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
