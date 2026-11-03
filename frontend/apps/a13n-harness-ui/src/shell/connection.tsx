import { createContext } from "react";
import { Button } from "a13n-ui";
import { WifiSlashIcon } from "@phosphor-icons/react";
import { useTranslation } from "../i18n";
import styles from "./workbench.module.css";

// Presentation only: stream, query and mutation lifecycles keep their owners.
export const ConnectionNoticeContext = createContext(false);

export function ConnectionNotice({ retry }: { retry: () => void }) {
  const { t } = useTranslation();
  return (
    <div
      role="status"
      aria-label={t("Server connection")}
      className={styles.connectionNotice}
    >
      <WifiSlashIcon size={18} aria-hidden="true" />
      <div className={styles.connectionMessage}>
        <strong>{t("Connection interrupted")}</strong>
        <span>{t("Reconnecting automatically…")}</span>
      </div>
      <Button variant="ghost" size="sm" onClick={retry}>
        {t("Retry now")}
      </Button>
    </div>
  );
}
