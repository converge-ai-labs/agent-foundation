import { useTranslation } from "react-i18next";
import styles from "./diff.module.css";

/**
 * Unified diff hunks, each a `@@ -a,b +c,d @@` header and its lines. Additions
 * and removals are read by tint and sign, never by a filled block.
 */
export function Patch({ hunks }: { hunks: readonly string[] }) {
  const { t } = useTranslation();
  if (!hunks.length)
    return <p className={styles.note}>{t("The file did not change.")}</p>;
  return (
    <div className={`${styles.patch} a13n-scrollbar`}>
      <pre>
        {hunks.map((hunk, index) => (
          <span key={index} className={styles.hunk}>
            {/* "\ No newline at end of file" is a diff artifact, not a change. */}
            {hunk
              .split("\n")
              .filter((line) => !line.startsWith("\\"))
              .map((line, position) => (
                <span
                  key={position}
                  className={styles.line}
                  data-sign={sign(line)}
                >
                  {line || " "}
                </span>
              ))}
          </span>
        ))}
      </pre>
    </div>
  );
}

function sign(line: string) {
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+")) return "added";
  if (line.startsWith("-")) return "removed";
  return "context";
}
