import { ArrowClockwiseIcon } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import styles from "./time-range.module.css";

/** The toolbar's last control: reload the view now, turning while it loads. */
export function RefreshButton({
  pending = false,
  onRefresh,
}: {
  pending?: boolean;
  onRefresh: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Button
      type="button"
      variant="outline"
      size="icon"
      aria-label={t("Refresh")}
      aria-busy={pending || undefined}
      title={t("Refresh")}
      onClick={onRefresh}
    >
      <ArrowClockwiseIcon
        aria-hidden="true"
        className={pending ? styles.spinning : undefined}
      />
    </Button>
  );
}
