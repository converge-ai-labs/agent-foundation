import { Button, DisclosureSection } from "a13n-ui";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../../shared/api";
import { ErrorNotice } from "../../../shared/feedback";
import { JsonView } from "../../../shared/forms";
import { useAgent } from "../../agents/queries";
import { isActiveRun, type ViewLevel } from "../api";
import { useRun } from "../queries";
import { useRunDisplay } from "../run-display";
import { runTimeline } from "../timeline";
import { WorkingRow } from "./assistant-message";
import { DebugRunSection } from "./debug/run-section";
import { DroppedItems } from "./dropped-items";
import { FailureNotice } from "./failure-notice";
import { RunBlock } from "./run-block";
import { useThreadRuns } from "./thread-runs";
import debug from "./debug/debug.module.css";
import styles from "./transcript.module.css";

/** A displayed run keeps its output and expanded rows when its successor starts. */
export function RunEntry({
  runId,
  thread,
  level,
  followed,
  prefill,
  jumpToDock,
}: {
  runId: string;
  thread: Schema["ThreadView"];
  level: ViewLevel;
  followed: boolean;
  prefill?: () => void;
  jumpToDock: () => void;
}) {
  const { t } = useTranslation();
  const query = useRun(runId);
  const run = query.data;
  const agent = useAgent(run?.agent_id);
  const agentName = agent.data?.name;
  const agentImageUrl = agent.data?.image_url;
  const live = useRunDisplay(runId, { live: followed });
  const { number } = useThreadRuns(level === "debug" ? thread.id : "");
  const timeline = useMemo(
    () =>
      run &&
      runTimeline({
        run,
        attempts: live.attempts,
        items: live.items,
        execution: live.execution,
        coverage: live.execution.coverage,
      }),
    [run, live.attempts, live.items, live.execution],
  );
  if (!run || !timeline) return <ErrorNotice error={query.error} />;
  const active = isActiveRun(run.status);
  const stopped = ["failed", "cancelled"].includes(run.status);
  return (
    <>
      {live.gap && (
        <p role="status" className={styles.notice}>
          {live.incomplete
            ? t(
                "Saved message history is incomplete or unconfirmed. Some output may be unavailable.",
              )
            : t(
                "Some live output was skipped. It appears when the run reaches its next checkpoint.",
              )}
        </p>
      )}
      {live.state === "disconnected" && (
        <ErrorNotice
          error={live.error ?? new Error(t("Live connection disconnected."))}
          retry={live.reconnect}
        />
      )}
      {level === "debug" ? (
        <>
          <DroppedItems count={live.dropped} />
          <DebugRunSection
            run={run}
            thread={thread}
            timeline={timeline}
            index={number(run.id)}
            resubmit={prefill}
            jumpToDock={jumpToDock}
          />
        </>
      ) : (
        <div className={debug.runAnchor} data-run={run.id}>
          <RunBlock
            run={run}
            thread={thread}
            timeline={timeline}
            agentName={agentName}
            agentImageUrl={agentImageUrl}
            earlier={<DroppedItems count={live.dropped} />}
          >
            {active && <WorkingRow connected={live.state === "connected"} />}
            {(run.failure != null || stopped) && (
              <FailureNotice
                failure={run.failure}
                cancelled={run.status === "cancelled"}
                action={
                  prefill && (
                    <Button size="sm" variant="outline" onClick={prefill}>
                      {t("Resubmit")}
                    </Button>
                  )
                }
              />
            )}
            {run.output != null && typeof run.output !== "string" && (
              <DisclosureSection
                className={styles.structuredOutput}
                defaultOpen
                title={<>{t("Structured output")}</>}
              >
                <JsonView value={run.output} />
              </DisclosureSection>
            )}
          </RunBlock>
        </div>
      )}
    </>
  );
}
