import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import styles from "./credentials.module.css";

const labels = [
  ["applied", "Current version confirmed"],
  ["pending", "Awaiting active synchronization"],
  ["retry", "Awaiting retry"],
  ["on_next_use", "Waiting for next use"],
  ["on_create", "Awaiting Session creation"],
  ["unknown", "Unconfirmed Sessions"],
] as const;

export function ApplicationSummary({
  summary,
}: {
  summary: Schema["SessionApplicationSummary"];
}) {
  const { t } = useTranslation();
  return (
    <section aria-label={t("All Session bindings")}>
      <h3>{t("All Session bindings")}</h3>
      <p className={styles.note}>
        {t(
          "Counts cover all pages. Each binding version is counted once; it is confirmed only when every selected credential is applied.",
        )}
      </p>
      <dl className={styles.applicationCounts}>
        {labels.map(([status, label]) => (
          <div key={status}>
            <dt>{t(label)}</dt>
            <dd>{summary[status]}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
