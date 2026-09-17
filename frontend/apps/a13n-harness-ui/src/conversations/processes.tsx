import { Badge } from "a13n-ui";
import type { ProcessObservation } from "./process-observations";
import styles from "./work-inspector.module.css";

function status(item: ProcessObservation) {
  if (item.phase === "exited" && item.exitCode)
    return `Failed (exit ${item.exitCode})`;
  return item.phase.replaceAll("_", " ");
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
      {!observations.length && <p>No background processes observed.</p>}
      <ul className={styles.tasks}>
        {observations
          .slice(-16)
          .reverse()
          .map((item) => (
            <li key={`${item.runId}:${item.processId}`}>
              <details>
                <summary className={styles.processSummary}>
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
                  </Badge>{" "}
                  <code>{item.command || "Command unavailable"}</code>
                </summary>
                <dl>
                  <div>
                    <dt>Process</dt>
                    <dd>{item.processId}</dd>
                  </div>
                  <div>
                    <dt>Run</dt>
                    <dd>{item.runId}</dd>
                  </div>
                  {item.exitCode !== undefined && (
                    <div>
                      <dt>Exit code</dt>
                      <dd>{item.exitCode}</dd>
                    </div>
                  )}
                </dl>
              </details>
            </li>
          ))}
      </ul>
      {(omitted || observations.length > 16) && (
        <p>Older observations omitted. Showing up to 16 processes.</p>
      )}
      {!!observations.length && (
        <p className={styles.caption}>
          Output remains in the corresponding tool details. Inspection does not
          control or stop processes.
        </p>
      )}
    </>
  );
}
