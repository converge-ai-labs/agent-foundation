import { createContext } from "react";
import { Button } from "a13n-ui";
import { WifiSlashIcon } from "@phosphor-icons/react";
import styles from "./workbench.module.css";

// Presentation only: stream, query and mutation lifecycles keep their owners.
export const ConnectionNoticeContext = createContext(false);

export function ConnectionNotice({ retry }: { retry: () => void }) {
  return (
    <div
      role="status"
      aria-label="Server connection"
      className={styles.connectionNotice}
    >
      <WifiSlashIcon size={18} aria-hidden="true" />
      <div className={styles.connectionMessage}>
        <strong>Connection interrupted</strong>
        <span>
          Reconnecting automatically. If the server was stopped, restart it.
        </span>
      </div>
      <Button variant="ghost" size="sm" onClick={retry}>
        Retry now
      </Button>
    </div>
  );
}
