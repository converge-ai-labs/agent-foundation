import type { ComponentProps } from "react";
import { ErrorNotice } from "../../shared/feedback";
import { Skeleton } from "a13n-ui";
import { useTranslation } from "react-i18next";
import styles from "./conversations.module.css";

/** One opening surface across route code, target resolution and the first display read. */
export function SessionBodyLoading() {
  const { t } = useTranslation();
  return (
    <div
      className={styles.sessionLoading}
      role="status"
      aria-busy="true"
      data-session-loading
    >
      <span className="sr-only">{t("Loading…")}</span>
      <div aria-hidden="true">
        <Skeleton className={styles.loadingMessage} />
        <Skeleton className={styles.loadingReply} />
        <Skeleton className={styles.loadingLine} />
      </div>
    </div>
  );
}

export function SessionRouteLoading() {
  return (
    <div className={styles.sessionDetail}>
      <div className={styles.sessionHeader} aria-hidden="true">
        <Skeleton className={styles.loadingTitle} />
      </div>
      <div className={styles.sessionContent}>
        <SessionBodyLoading />
      </div>
    </div>
  );
}

/** Route-level failures share the content gutter even when no transcript can mount. */
export function SessionError(props: ComponentProps<typeof ErrorNotice>) {
  return props.error ? (
    <div className={styles.sessionNotice}>
      <ErrorNotice {...props} />
    </div>
  ) : null;
}
