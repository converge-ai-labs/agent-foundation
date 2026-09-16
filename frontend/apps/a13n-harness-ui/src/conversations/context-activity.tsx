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
  return (
    <details className={styles.contextActivity} data-kind={context}>
      <summary>
        <span>{context === "handoff" ? "Summary" : "Compact Summary"}</span>
        <span className={styles.contextStatus}>
          {status || (text ? "Saved" : "In progress")}
        </span>
        {text && <span className={styles.contextPreview}>{preview(text)}</span>}
      </summary>
      {text && <MessageText text={text} />}
    </details>
  );
}
