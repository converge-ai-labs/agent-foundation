import { useEffect, useState } from "react";
import styles from "./conversation.module.css";

export function LiveConnectionNotice({
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
    const timer = setTimeout(() => setDelayed(true), 1000);
    return () => clearTimeout(timer);
  }, [pending]);
  if (!reconnections && (!pending || !delayed)) return null;
  return (
    <div className={styles.activityBar}>
      <small role="status">
        {reconnections > 0
          ? `${connection === "Live" ? "Live connection restored" : "Reconnecting live updates…"} · ${reconnections} ${reconnections === 1 ? "retry" : "retries"}`
          : connection}
      </small>
    </div>
  );
}
