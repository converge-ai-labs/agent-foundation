import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { diffSections, type DiffLine } from "./lines";
import styles from "./diff.module.css";

/** Editor-style display of unified hunks; narrow readers use inline rows. */
export function Patch({
  hunks,
  expanded = false,
  beforeLabel,
  afterLabel,
}: {
  hunks: readonly string[];
  /** Let the enclosing file reader own vertical scrolling. */
  expanded?: boolean;
  beforeLabel?: string;
  afterLabel?: string;
}) {
  const { t } = useTranslation();
  const sections = useMemo(() => diffSections(hunks), [hunks]);
  if (!sections.length)
    return <p className={styles.note}>{t("The file did not change.")}</p>;
  return (
    <div
      className={`${styles.patch} a13n-scrollbar`}
      data-expanded={expanded || undefined}
    >
      <div className={styles.editor}>
        <div className={styles.labels}>
          <span>{beforeLabel ?? t("Before")}</span>
          <span>{afterLabel ?? t("After")}</span>
        </div>
        {sections.map((section, index) => (
          <section key={index}>
            {(index > 0 || section.before > 1 || section.after > 1) && (
              <div className={styles.gap}>
                <span aria-hidden="true">···</span>{" "}
                {t("Unchanged lines omitted")}
              </div>
            )}
            {section.rows.map((row, position) => (
              <div
                key={position}
                className={styles.row}
                data-context={row.context || undefined}
              >
                <Line line={row.before} side="before" context={row.context} />
                <Line line={row.after} side="after" context={row.context} />
              </div>
            ))}
          </section>
        ))}
      </div>
    </div>
  );
}

function Line({
  line,
  side,
  context,
}: {
  line?: DiffLine;
  side: "before" | "after";
  context?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div
      className={styles.line}
      data-side={side}
      data-kind={
        !line
          ? "empty"
          : context
            ? "context"
            : side === "before"
              ? "removed"
              : "added"
      }
    >
      {line && (
        <>
          <span className={styles.number} aria-hidden="true">
            {line.number}
          </span>
          <span className={styles.sign} aria-hidden="true">
            {context ? "" : side === "before" ? "−" : "+"}
          </span>
          <code>
            {line.parts
              ? line.parts.map((part, index) =>
                  part.changed ? (
                    <mark key={index}>{part.text}</mark>
                  ) : (
                    part.text
                  ),
                )
              : line.text || "\u00a0"}
            {line.noNewline && (
              <span
                className={styles.noNewline}
                title={t("No newline at end of file")}
                aria-label={t("No newline at end of file")}
              >
                ↵̸
              </span>
            )}
          </code>
        </>
      )}
    </div>
  );
}
