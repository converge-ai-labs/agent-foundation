import { Button } from "a13n-ui";
import { useLayoutEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../../../shared/feedback";
import type { useRunHistory } from "../run-history";
import styles from "./transcript.module.css";

/** Unloaded history is a reader action, distinct from missing or failed content. */
export function EarlierContent({
  history,
  first,
  preservePosition,
}: {
  history: ReturnType<typeof useRunHistory>;
  first: number;
  preservePosition: () => () => void;
}) {
  const { t } = useTranslation();
  const restore = useRef<(() => void) | undefined>(undefined);
  useLayoutEffect(() => {
    restore.current?.();
    restore.current = undefined;
  });
  if (first <= 1) return null;
  const load = () =>
    history.loadEarlier(first, () => {
      restore.current = preservePosition();
    });
  return (
    <div className={styles.earlierRuns}>
      <ErrorNotice error={history.earlier.error} retry={load} />
      <Button
        type="button"
        size="sm"
        variant="ghost"
        loading={history.earlier.isPending}
        disabled={history.earlier.isPending || history.read.isFetching}
        onClick={load}
      >
        {t("Load earlier content")}
      </Button>
    </div>
  );
}
