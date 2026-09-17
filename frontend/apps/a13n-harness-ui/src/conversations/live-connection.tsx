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
    const timer = setTimeout(() => setDelayed(true), 700);
    return () => clearTimeout(timer);
  }, [pending]);
  if (!pending || !delayed) return null;
  return (
    <div className={styles.activityBar}>
      <small role="status">
        {reconnections > 0
          ? `Reconnecting live updates… · ${reconnections} ${reconnections === 1 ? "retry" : "retries"}`
          : connection === "Connecting"
            ? "Connecting live updates…"
            : connection}
      </small>
    </div>
  );
}
