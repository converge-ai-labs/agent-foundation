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
  const label =
    connection === "Loading current output"
      ? "Syncing…"
      : reconnections > 0 || connection === "Reconnecting"
        ? "Reconnecting…"
        : "Connecting…";
  return (
    <Popover>
      <span role="status" className="sr-only">
        {connection === "Loading current output"
          ? connection
          : `${label.slice(0, -1)} live updates…`}
      </span>
      <PopoverTrigger
        render={<Button variant="ghost" size="sm" tooltip={false} />}
        className={styles.trigger}
        aria-label="Live updates connection"
      >
        <Plugs className={styles.icon} aria-hidden="true" />
        <span className={styles.label}>{label}</span>
      </PopoverTrigger>
      <PopoverPopup side="top" align="start" className={styles.popup}>
        <PopoverTitle>Live updates</PopoverTitle>
        <p>{connection}</p>
        <p>Updates will resume automatically when the connection is ready.</p>
        {reconnections > 0 && (
          <p>
            {reconnections} {reconnections === 1 ? "retry" : "retries"}
          </p>
        )}
      </PopoverPopup>
    </Popover>
  );
}
