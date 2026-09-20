import { Button, DisclosureSection } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useRef } from "react";
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
import { useRunStream } from "../run-stream";
import { useRun } from "../queries";
import { runTimeline } from "../timeline";
import { WorkingRow } from "./assistant-message";
import { DebugRunSection } from "./debug/run-section";
import { RunNavigator } from "./debug/run-navigator";
import { useThreadRuns } from "./thread-runs";
import { useViewLevel } from "./debug/view";
import { EarlierMessages } from "./earlier-messages";
import { FailureNotice } from "./failure-notice";
import { HistoryTranscript } from "./history";
import { RunBlock } from "./run-block";
import { RunDock } from "./run-dock";
import {
  isInteractive,
  useRetryRun,
  useRunAcceptance,
  type ConfigurationBridge,
} from "./run-actions";
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
 * on it below. The configuration assistant embeds the same transcript at the
 * Chat level and keeps its own composer.
 */
export function RunContent({
  runId,
  threadId,
  sessionId,
  configuration,
}: {
  runId: string;
  threadId: string;
  sessionId: string;
  configuration?: ConfigurationBridge;
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
  const run = runQuery.data;
  const thread = threadQuery.data;
  const agent = useAgent(configuration ? undefined : run?.agent_id);
  const { level } = useViewLevel(thread, { chatOnly: !!configuration });

  if (runQuery.isPending || threadQuery.isPending)
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
      agentName={
        configuration ? t("Configuration assistant") : agent.data?.name
      }
      agentImageUrl={agent.data?.image_url}
      configuration={configuration}
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
  configuration,
  error,
}: {
  run: Schema["RunResource"];
  thread: Schema["ThreadResource"];
  level: ViewLevel;
  agentName?: string;
  agentImageUrl?: string | null;
  configuration?: ConfigurationBridge;
  error: unknown;
}) {
  const { t } = useTranslation();
  const { can } = useWorkspace();
  const transcript = useRef<HTMLDivElement>(null);
  // Only an origin replay observes a complete execution history, and only
  // Debug renders one; Chat keeps the cheaper snapshot attachment.
  const live = useRunStream(run.id, { replay: level === "debug" });
  const scroll = useTranscriptScroll(transcript, true);
  const { number } = useThreadRuns(level === "debug" ? thread.id : "");
  // One reading of the run for both levels: Chat and Debug present the same
  // entries, so they can never disagree on what happened.
  const timeline = useMemo(
    () =>
      runTimeline({
        run,
        items: live.items,
        execution: live.execution,
        coverage: live.execution.coverage,
      }),
    [run, live.items, live.execution],
  );
  const { accepted, refresh } = useRunAcceptance(run, thread, configuration);
  const retry = useRetryRun(run, thread, accepted, refresh);
  const active = isActiveRun(run.status);
  const stopped = ["failed", "cancelled"].includes(run.status);
  const canRetry =
    isInteractive(thread, configuration) && stopped && can("run.retry");
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
          {!live.hasEarlier && (
            <HistoryTranscript runId={run.id} thread={thread} level={level} />
          )}
          <EarlierMessages {...live} />
          <DebugRunSection
            run={run}
            thread={thread}
            timeline={timeline}
            index={number(run.id)}
            runNumber={number}
          />
        </div>
      ) : (
        <div className={styles.transcript} ref={transcript}>
          {!live.hasEarlier && (
            <HistoryTranscript runId={run.id} thread={thread} level={level} />
          )}
          <div className={debug.runAnchor} data-run={run.id}>
            <RunBlock
              run={run}
              thread={thread}
              timeline={timeline}
              agentName={agentName}
              agentImageUrl={agentImageUrl}
              earlier={<EarlierMessages {...live} />}
            >
              {active && <WorkingRow connected={live.state === "connected"} />}
              {(run.failure != null || stopped) && (
                <FailureNotice
                  failure={run.failure}
                  cancelled={run.status === "cancelled"}
                  action={
                    canRetry ? (
                      <Button
                        size="sm"
                        variant="outline"
                        loading={retry.isPending}
                        onClick={() => retry.mutate()}
                      >
                        {t("Retry run")}
                      </Button>
                    ) : undefined
                  }
                />
              )}
              {run.output != null && run.output !== run.output_text && (
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
        configuration={configuration}
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
