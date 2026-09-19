import { useTranslation } from "react-i18next";
import { CopyButton } from "../identity";
import styles from "./secret-reveal.module.css";

/**
 * A value the service shows once: the label, the value on a quiet surface in
 * monospace, a copy affordance, and the sentence that says it will not return.
 */
export function SecretReveal({
  value,
  label,
  caution,
  wrap = false,
  copyLabel,
}: {
  value: string;
  label?: string;
  caution?: string;
  wrap?: boolean;
  copyLabel?: string;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.reveal}>
      <div className={styles.head}>
        <span className={styles.label}>{label ?? t("One-time value")}</span>
        <CopyButton value={value} copyLabel={copyLabel ?? t("Copy")} />
      </div>
      <code className={styles.value} data-wrap={wrap ? "true" : undefined}>
        {value}
      </code>
      <p className={styles.caution}>
        {caution ?? t("Copy this value now. It will not be shown again.")}
      </p>
    </div>
  );
}
