import { Badge } from "a13n-ui";
import type { ProcessObservation } from "./process-observations";
import { DebugOnly } from "../shell/debug";
import styles from "./work-inspector.module.css";

function status(item: ProcessObservation) {
  if (item.phase === "exited" && item.exitCode)
    return `Failed (exit ${item.exitCode})`;
  const label = item.phase.replaceAll("_", " ");
  return label.charAt(0).toUpperCase() + label.slice(1);
}
export function Processes({
  observations,
  omitted,
  incomplete,
}: {
  observations: ProcessObservation[];
  omitted: boolean;
  incomplete: boolean;
}) {
  return (
    <>
      {incomplete && (
        <p className={styles.caption}>
          Live observations are incomplete. Status may be stale.
        </p>
      )}
      {!observations.length && (
        <p className={styles.emptyState}>No background processes observed.</p>
      )}
      <ul className={styles.tasks}>
        {observations
          .slice(-16)
          .reverse()
          .map((item) => (
            <li key={`${item.runId}:${item.processId}`}>
              <details>
                <summary className={styles.processSummary}>
                  <code>{item.command || "Command unavailable"}</code>
                  <Badge
                    variant={
                      item.phase === "failed" ||
                      item.phase === "timed_out" ||
                      (item.phase === "exited" && !!item.exitCode)
                        ? "error"
                        : item.phase === "running"
                          ? "info"
                          : "secondary"
                    }
                  >
                    {status(item)}
                  </Badge>
                </summary>
                <div className={styles.executionBody}>
                  <dl>
                    <DebugOnly>
                      <div>
                        <dt>Process</dt>
                        <dd>{item.processId}</dd>
                      </div>
                      <div>
                        <dt>Run</dt>
                        <dd>{item.runId}</dd>
                      </div>
                    </DebugOnly>
                    {item.exitCode !== undefined && (
                      <div>
                        <dt>Exit code</dt>
                        <dd>{item.exitCode}</dd>
                      </div>
                    )}
                  </dl>
                  <p className={styles.caption}>
                    Output is in the corresponding tool call.
                  </p>
                </div>
              </details>
            </li>
          ))}
      </ul>
      {(omitted || observations.length > 16) && (
        <p>Older observations omitted. Showing up to 16 processes.</p>
      )}
      {!!observations.length && (
        <p className={styles.caption}>
          Inspection does not control or stop processes.
        </p>
      )}
    </>
  );
}
