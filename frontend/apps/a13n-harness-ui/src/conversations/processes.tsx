import { useTranslation } from "../i18n";
import type { TFunction } from "i18next";
import { Badge } from "a13n-ui";
import type { ProcessObservation } from "./process-observations";
import { DebugOnly } from "../shell/debug";
import styles from "./work-inspector.module.css";

function status(item: ProcessObservation, t: TFunction) {
  if (item.phase === "exited" && item.exitCode)
    return t("Failed (exit {{code}})", { code: item.exitCode });
  const labels: Record<string, string> = {
    running: t("Running"),
    exited: t("Exited"),
    signaled: t("Signaled"),
    timed_out: t("Timed out"),
    cancelled: t("Cancelled"),
    failed: t("Failed"),
    unavailable: t("Unavailable"),
  };
  const label = item.phase.replaceAll("_", " ");
  return labels[item.phase] ?? label.charAt(0).toUpperCase() + label.slice(1);
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
  const { t } = useTranslation();
  return (
    <>
      {incomplete && (
        <p className={styles.caption}>
          {t("Live observations are incomplete. Status may be stale.")}
        </p>
      )}
      {!observations.length && (
        <p className={styles.emptyState}>
          {t("No background processes observed.")}
        </p>
      )}
      <ul className={styles.tasks}>
        {observations
          .slice(-16)
          .reverse()
          .map((item) => (
            <li key={`${item.runId}:${item.processId}`}>
              <details>
                <summary className={styles.processSummary}>
                  <code>{item.command || t("Command unavailable")}</code>
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
                    {status(item, t)}
                  </Badge>
                </summary>
                <div className={styles.executionBody}>
                  <dl>
                    <DebugOnly>
                      <div>
                        <dt>{t("Process")}</dt>
                        <dd>{item.processId}</dd>
                      </div>
                      <div>
                        <dt>{t("Run")}</dt>
                        <dd>{item.runId}</dd>
                      </div>
                    </DebugOnly>
                    {item.exitCode !== undefined && (
                      <div>
                        <dt>{t("Exit code")}</dt>
                        <dd>{item.exitCode}</dd>
                      </div>
                    )}
                  </dl>
                  <p className={styles.caption}>
                    {t("Output is in the corresponding tool call.")}
                  </p>
                </div>
              </details>
            </li>
          ))}
      </ul>
      {(omitted || observations.length > 16) && (
        <p>{t("Older observations omitted. Showing up to 16 processes.")}</p>
      )}
      {!!observations.length && (
        <p className={styles.caption}>
          {t("Inspection does not control or stop processes.")}
        </p>
      )}
    </>
  );
}
