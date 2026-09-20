import { Button, ModalFrame } from "a13n-ui";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { JsonView } from "../../../../shared/forms";
import styles from "./pane.module.css";

/** One labelled block inside an expanded row. */
export function PaneSection({
  label,
  action,
  children,
}: {
  label: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={styles.paneSection}>
      <div className={styles.paneLabel}>
        <span>{label}</span>
        {action}
      </div>
      {children}
    </section>
  );
}

/**
 * A retained value as it was reported: text stays text, structures are printed.
 * Bounded so one long result cannot push the timeline off the screen.
 */
export function PaneValue({ value }: { value: unknown }) {
  const { t } = useTranslation();
  if (value === undefined || value === null)
    return <p className={styles.paneNote}>{t("Nothing was reported.")}</p>;
  const text =
    typeof value === "string" ? value : (JSON.stringify(value, null, 2) ?? "");
  if (!text.trim())
    return <p className={styles.paneNote}>{t("Nothing was reported.")}</p>;
  return <pre className={`${styles.paneCode} a13n-scrollbar`}>{text}</pre>;
}

/** The untouched payload, in the shared named dialog. */
export function RawDialog({
  title,
  value,
  label,
}: {
  title: string;
  value: unknown;
  label?: string;
}) {
  const { t } = useTranslation();
  return (
    <ModalFrame
      trigger={
        <Button
          variant="ghost"
          size="sm"
          className={styles.rawTrigger}
          type="button"
        >
          {label ?? t("Raw")}
        </Button>
      }
      size="lg"
      title={title}
      closeLabel={t("Close")}
    >
      <JsonView value={value} />
    </ModalFrame>
  );
}

/** The facts strip at the top of a pane: tabular, quiet, one line when it fits. */
export function PaneStats({
  facts,
  action,
}: {
  facts: (string | null | false | undefined)[];
  action?: ReactNode;
}) {
  const shown = facts.filter((fact): fact is string => !!fact);
  if (!shown.length && !action) return null;
  return (
    <div className={styles.paneStats}>
      <span className={styles.paneFacts}>
        {shown.map((fact, index) => (
          <span key={index}>{fact}</span>
        ))}
      </span>
      {action}
    </div>
  );
}
