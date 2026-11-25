import type { ToolProgress } from "../display";
import styles from "./tool-progress.module.css";

export function ToolProgressLine({
  value,
  label,
}: {
  value: ToolProgress;
  label: string;
}) {
  const { progress, total, message } = value;
  const percent =
    progress !== null && total !== null && total > 0
      ? Math.max(0, Math.min(100, (progress / total) * 100))
      : null;
  if (progress === null && !message) return null;
  return (
    <div className={styles.progress}>
      <div className={styles.line}>
        {message && (
          <span className={styles.message} title={message}>
            {message}
          </span>
        )}
        {(percent !== null || (!message && progress !== null)) && (
          <span className={styles.count}>
            {percent === null ? progress : `${Math.round(percent)}%`}
          </span>
        )}
      </div>
      {percent !== null && (
        <div
          className={styles.track}
          role="progressbar"
          aria-label={message || label}
          aria-valuenow={Math.round(percent)}
        >
          <div className={styles.fill} style={{ width: `${percent}%` }} />
        </div>
      )}
    </div>
  );
}
