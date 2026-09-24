import { Button, DisclosureSection } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import { useParams } from "react-router";
import { ArrowDownIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import type { Schema } from "../../../shared/api";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import { JsonView } from "../../../shared/forms";
import { useAgent } from "../../agents/queries";
import { conversationQueries, isActiveRun, type ViewLevel } from "../api";
import { useRunDisplay } from "../run-display";
import { useRun, useSession } from "../queries";
import { runResubmission, type Resubmission } from "../resubmit";
import { runTimeline } from "../timeline";
import { WorkingRow } from "./assistant-message";
import { DebugRunSection } from "./debug/run-section";
import { DroppedItems } from "./dropped-items";
import { RunNavigator } from "./debug/run-navigator";
import { useThreadRuns } from "./thread-runs";
import { useViewLevel } from "./debug/view";
import { FailureNotice } from "./failure-notice";
import { HistoryTranscript } from "./history";
import { RunBlock } from "./run-block";
import { RunDock } from "./run-dock";
import { isInteractive } from "./run-actions";
import { useTranscriptScroll } from "./use-transcript-scroll";
import debug from "./debug/debug.module.css";
import styles from "./transcript.module.css";

export function RunPage() {
  const { runId = "", threadId = "", sessionId = "" } = useParams();
  return (
    <RunContent
      key={runId}
      runId={runId}
      threadId={threadId}
      sessionId={sessionId}
    />
  );
}

/**
 * The run being followed, with its ancestors above it and the controls that act
 * on it below.
 */
export function RunContent({
  runId,
  threadId,
  sessionId,
}: {
  runId: string;
  threadId: string;
  sessionId: string;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace(),
    queries = conversationQueries(client, workspace.id);
  const runQuery = useRun(runId);
  const threadQuery = useQuery({
    ...queries.thread(threadId),
    enabled: !!threadId,
  });
  const session = useSession(sessionId);
  const run = runQuery.data;
  const thread = threadQuery.data;
  const agent = useAgent(run?.agent_id);
  const { level } = useViewLevel(thread, session.data);

  // The Session decides the level a Thread opens at, so it is read first.
  if (runQuery.isPending || threadQuery.isPending || session.isPending)
    return <Loading variant="detail" />;
  if (!run || !thread)
    return (
      <ErrorNotice
        error={runQuery.error ?? threadQuery.error}
        retry={() => {
          void runQuery.refetch();
          void threadQuery.refetch();
        }}
      />
    );
  if (run.thread_id !== threadId || run.session_id !== sessionId)
    return (
      <ErrorNotice
        error={new Error(t("This run does not belong to this thread."))}
      />
    );
  return (
    <RunBody
      run={run}
      thread={thread}
      level={level}
      agentName={agent.data?.name}
      agentImageUrl={agent.data?.image_url}
      error={runQuery.error ?? threadQuery.error}
    />
  );
}

function RunBody({
  run,
  thread,
  level,
  agentName,
  agentImageUrl,
  error,
}: {
  run: Schema["RunView"];
  thread: Schema["ThreadView"];
  level: ViewLevel;
  agentName?: string;
  agentImageUrl?: string | null;
  error: unknown;
}) {
  const { t } = useTranslation();
  const { can } = useWorkspace();
  const transcript = useRef<HTMLDivElement>(null);
  const live = useRunDisplay(run.id, { live: true });
  const scroll = useTranscriptScroll(transcript, true);
  const { number } = useThreadRuns(level === "debug" ? thread.id : "");
  // One reading of the run for both levels: Chat and Debug present the same
  // entries, so they can never disagree on what happened.
  const timeline = useMemo(
    () =>
      runTimeline({
        run,
        attempts: live.attempts,
        items: live.items,
        execution: live.execution,
        coverage: live.execution.coverage,
      }),
    [run, live.attempts, live.items, live.execution],
  );
  const active = isActiveRun(run.status);
  const stopped = ["failed", "cancelled"].includes(run.status);
  // A stopped Run is asked again from the dock, while it is still the
  // Thread's latest: its message is prefilled there to be sent again.
  const [resubmit, setResubmit] = useState<Resubmission | null>(null);
  const resubmission =
    stopped &&
    isInteractive(thread) &&
    can("run") &&
    (thread.current_run_id ?? thread.last_run_id) === run.id
      ? runResubmission(run)
      : null;
  const prefill = resubmission
    ? () => {
        setResubmit(resubmission);
        scroll.jumpToLatest();
      }
    : undefined;
  return (
    <div className={styles.run}>
      <ErrorNotice error={error} />
      {live.gap && (
        <p role="status" className={styles.notice}>
          {live.incomplete
            ? t(
                "Saved message history is incomplete or unconfirmed. Some output may be unavailable.",
              )
            : t(
                "Live replay resumed from saved messages. Earlier raw events may be unavailable.",
              )}
        </p>
      )}
      {live.state === "disconnected" && (
        <ErrorNotice
          error={live.error ?? new Error(t("Live connection disconnected."))}
          retry={live.reconnect}
        />
      )}
      {level === "debug" && <RunNavigator thread={thread} runId={run.id} />}
      {level === "debug" ? (
        <div className={debug.sections} ref={transcript}>
          <HistoryTranscript runId={run.id} thread={thread} level={level} />
          <DroppedItems count={live.dropped} />
          <DebugRunSection
            run={run}
            thread={thread}
            timeline={timeline}
            index={number(run.id)}
            resubmit={prefill}
          />
        </div>
      ) : (
        <div className={styles.transcript} ref={transcript}>
          <HistoryTranscript runId={run.id} thread={thread} level={level} />
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
        </div>
      )}
      <RunDock
        run={run}
        thread={thread}
        agentName={agentName}
        resubmit={resubmit}
        onResubmitted={() => setResubmit(null)}
        above={
          !scroll.following && (
            <Button
              className={styles.jumpToLatest}
              size="sm"
              variant="outline"
              onClick={scroll.jumpToLatest}
              type="button"
            >
              <ArrowDownIcon size={14} />
              {t("Jump to latest")}
            </Button>
          )
        }
      />
    </div>
  );
}
