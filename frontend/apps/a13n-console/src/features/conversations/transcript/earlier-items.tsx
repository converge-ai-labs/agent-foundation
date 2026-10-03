import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../../../shared/feedback";
import type { EarlierItems as Earlier } from "../earlier-items";
import styles from "./transcript.module.css";

/** Reads a Run's earlier Items, a page at a time, ahead of those shown. */
export function EarlierItems({ earlier }: { earlier: Earlier }) {
  const { t } = useTranslation();
  if (earlier.error)
    return <ErrorNotice error={earlier.error} retry={earlier.load} />;
  if (!earlier.more) return null;
  return (
    <p className={styles.notice}>
      <Button
        size="sm"
        variant="ghost"
        disabled={earlier.loading}
        onClick={earlier.load}
      >
        {t("Show earlier items")}
      </Button>
    </p>
  );
}
