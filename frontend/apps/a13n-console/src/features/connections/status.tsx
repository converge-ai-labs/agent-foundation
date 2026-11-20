import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { StatePill } from "../../shared/feedback";
import { connectionState } from "./api";
import styles from "./connections.module.css";

export function useConnectionStateLabels() {
  const { t } = useTranslation();
  return {
    ready: t("Connected"),
    unauthorized: t("Not authorized"),
    authorizing: t("Authorizing"),
    reauthorization_required: t("Reauthorization required"),
    reauthorizing: t("Reauthorizing"),
    disabled: t("Disabled"),
  };
}

/** Connection-specific labels stay out of the shared state vocabulary. */
export function ConnectionStatus({
  connection,
}: {
  connection: Schema["Connection"];
}) {
  const { t } = useTranslation();
  const labels = useConnectionStateLabels();
  const state = connectionState(connection);
  return (
    <span className={styles.status}>
      <StatePill
        state={
          state === "authorizing" || state === "reauthorizing"
            ? "connecting"
            : state
        }
        label={labels[state]}
      />
      {connection.enabled &&
        connection.status === "ready" &&
        connection.authorization_pending && (
          <span className={styles.statusNote}>
            {t("Reauthorization in progress")}
          </span>
        )}
    </span>
  );
}
