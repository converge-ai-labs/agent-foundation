import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Timestamp } from "../../../shared/feedback";
import styles from "./transcript.module.css";

/**
 * Runs are separated by a quiet rule rather than a card: the transcript reads
 * as one conversation, and the label says which run the next messages belong to.
 */
export function RunSeparator({
  createdAt,
  state,
  action,
}: {
  createdAt?: string | null;
  state?: string;
  action?: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.separator}>
      <span className={styles.rule} aria-hidden="true" />
      <span className={styles.separatorLabel}>
        {t("Run")}
        {createdAt && (
          <>
            <span aria-hidden="true">·</span>
            <Timestamp value={createdAt} relative />
          </>
        )}
        {state && (
          <>
            <span aria-hidden="true">·</span>
            {t(`state.${state}`, { defaultValue: state.replaceAll("_", " ") })}
          </>
        )}
      </span>
      {action}
      <span className={styles.rule} aria-hidden="true" />
    </div>
  );
}
