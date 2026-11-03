import { useTranslation } from "../i18n";
import { useEffect, useState } from "react";
import { Plugs } from "@phosphor-icons/react";
import {
  Button,
  Popover,
  PopoverTrigger,
  PopoverPopup,
  PopoverTitle,
} from "a13n-ui";
import styles from "./live-connection.module.css";

export function LiveConnectionStatus({
  connection,
  reconnections,
}: {
  connection: string;
  reconnections: number;
}) {
  const { t } = useTranslation();
  const [delayed, setDelayed] = useState(false);
  const pending = connection !== "Live";
  useEffect(() => {
    if (!pending) {
      setDelayed(false);
      return;
    }
    const timer = setTimeout(() => setDelayed(true), 1500);
    return () => clearTimeout(timer);
  }, [pending]);
  if (!pending || !delayed) return null;
  const syncing = connection === "Loading current output";
  const reconnecting =
    !syncing && (reconnections > 0 || connection === "Reconnecting");
  const label = syncing
    ? t("Syncing…")
    : reconnecting
      ? t("Reconnecting…")
      : t("Connecting…");
  // Known application-owned stream states; unknown transport values stay verbatim.
  const states: Record<string, string> = {
    Live: t("stream.live"),
    Connecting: t("stream.connecting"),
    Reconnecting: t("stream.reconnecting"),
    "Loading current output": t("stream.loading-current-output"),
  };
  return (
    <Popover>
      <span role="status" className="sr-only">
        {syncing
          ? (states[connection] ?? connection)
          : reconnecting
            ? t("Reconnecting live updates…")
            : t("Connecting live updates…")}
      </span>
      <PopoverTrigger
        render={<Button variant="ghost" size="sm" tooltip={false} />}
        className={styles.trigger}
        aria-label={t("Live updates connection")}
      >
        <Plugs className={styles.icon} aria-hidden="true" />
        <span className={styles.label}>{label}</span>
      </PopoverTrigger>
      <PopoverPopup side="top" align="start" className={styles.popup}>
        <PopoverTitle>{t("Live updates")}</PopoverTitle>
        <p>{states[connection] ?? connection}</p>
        <p>
          {t("Updates will resume automatically when the connection is ready.")}
        </p>
        {reconnections > 0 && (
          <p>{t("Retry count", { count: reconnections })}</p>
        )}
      </PopoverPopup>
    </Popover>
  );
}
