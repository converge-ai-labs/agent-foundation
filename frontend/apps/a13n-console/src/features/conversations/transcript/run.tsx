import { Button, DisclosureSection } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useRef, type RefObject } from "react";
import { useParams } from "react-router";
import { ArrowDownIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import type { Schema } from "../../../shared/api";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import { JsonView } from "../../../shared/forms";
import { useAgent } from "../../agents/queries";
import { conversationQueries, isActiveRun } from "../api";
import { useLiveRun } from "../live";
import { useRun } from "../queries";
import { WorkingRow } from "./assistant-message";
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
 * on it below. The configuration assistant embeds the same transcript and keeps
 * its own composer.
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
  const live = useLiveRun(runId);
  const runQuery = useRun(runId);
  const run = runQuery.data;
  const agent = useAgent(configuration ? undefined : run?.agent_id);
  const transcript = useRef<HTMLDivElement>(null);
  const threadQuery = useQuery({
    ...queries.thread(run?.thread_id ?? ""),
    enabled: !!run,
  });
  const thread = threadQuery.data;
  const scroll = useTranscriptScroll(transcript, !!run && !!thread);

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
      live={live}
      agentName={
        configuration ? t("Configuration assistant") : agent.data?.name
      }
      agentImageUrl={agent.data?.image_url}
      configuration={configuration}
      transcript={transcript}
      scroll={scroll}
      error={runQuery.error ?? threadQuery.error}
    />
  );
}

function RunBody({
  run,
  thread,
  live,
  agentName,
  agentImageUrl,
  configuration,
  transcript,
  scroll,
  error,
}: {
  run: Schema["RunResource"];
  thread: Schema["ThreadResource"];
  live: ReturnType<typeof useLiveRun>;
  agentName?: string;
  agentImageUrl?: string | null;
  configuration?: ConfigurationBridge;
  transcript: RefObject<HTMLDivElement | null>;
  scroll: ReturnType<typeof useTranscriptScroll>;
  error: unknown;
}) {
  const { t } = useTranslation();
  const { can } = useWorkspace();
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
                "Saved message history is incomplete. Some output is unavailable.",
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
      <div className={styles.transcript} ref={transcript}>
        {!live.hasEarlier && <HistoryTranscript runId={run.id} />}
        <RunBlock
          run={run}
          items={live.items}
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
