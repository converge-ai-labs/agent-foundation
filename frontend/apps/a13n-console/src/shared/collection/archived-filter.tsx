import { Button } from "a13n-ui";
import { ArchiveIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import styles from "./collection.module.css";

/** Toolbar chip that adds archived items to a head list of open ones. */
export function ArchivedFilter({
  value,
  onChange,
}: {
  value: boolean;
  onChange: (value: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <Button
      type="button"
      variant="outline"
      className={styles.archivedChip}
      data-active={value ? "true" : undefined}
      aria-pressed={value}
      onClick={() => onChange(!value)}
    >
      <ArchiveIcon size={13} aria-hidden="true" />
      {t("Archived")}
    </Button>
  );
}
