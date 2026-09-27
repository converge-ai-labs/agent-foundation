import { useTranslation } from "react-i18next";
import { CopyButton } from "./copy";
import styles from "./identity.module.css";

/** Key chip beside a detail-page title: the key with a copy affordance. */
export function ResourceKeyChip({ value }: { value: string }) {
  const { t } = useTranslation();
  return (
    <span className={styles.resourceKeyChip}>
      <span className={styles.resourceKeyText} title={value}>
        {value}
      </span>
      <CopyButton value={value} iconOnly copyLabel={t("Copy resource key")} />
    </span>
  );
}
