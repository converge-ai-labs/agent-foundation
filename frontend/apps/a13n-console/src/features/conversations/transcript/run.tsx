import { Button } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useLocation, useParams } from "react-router";
import { ArrowDownIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import type { Schema } from "../../../shared/api";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import { useAgent } from "../../agents/queries";
import { conversationQueries, type ViewLevel } from "../api";
import { useRun, useSession } from "../queries";
import { runResubmission, type Resubmission } from "../resubmit";
import { BranchOrigin } from "../thread-branches";
import { RunNavigator } from "./debug/run-navigator";
import { useViewLevel } from "./debug/view";
import { HistoryTranscript } from "./history";
import { RunEntry } from "./run-entry";
import { RunDock, type PendingMessage } from "./run-dock";
import { isInteractive } from "./run-actions";
import { useTranscriptScroll } from "./use-transcript-scroll";
import { UserMessage } from "./user-message";
import { inputText } from "../input";
import debug from "./debug/debug.module.css";
import styles from "./transcript.module.css";

export function RunPage() {
  const { runId = "", threadId = "", sessionId = "" } = useParams();
  const { state } = useLocation();
  const [shown, setShown] = useState([runId]);
  let runIds = shown;
  if (shown.at(-1) !== runId) {
    // Only a submission continues this view. History links and Back open the
    // requested run on its own, with that run's authoritative lineage.
    runIds =
      state?.continuedFrom === shown.at(-1) && !shown.includes(runId)
        ? [...shown, runId]
        : [runId];
    setShown(runIds);
  }
  return (
    <RunContent
      key={`${threadId}:${runIds[0]}`}
      runIds={runIds}
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
  runIds = [runId],
}: {
  runIds?: string[];
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
      runIds={runIds}
      thread={thread}
      level={level}
      agentName={agent.data?.name}
      error={runQuery.error ?? threadQuery.error}
    />
  );
}

function RunBody({
  run,
  runIds,
  thread,
  level,
  agentName,
  error,
}: {
  run: Schema["RunView"];
  runIds: string[];
  thread: Schema["ThreadView"];
  level: ViewLevel;
  agentName?: string;
  error: unknown;
}) {
  const { t } = useTranslation();
  const { can, workspace } = useWorkspace();
  const client = useClient();
  const threads = useQuery({
    ...conversationQueries(client, workspace.id).threads(thread.session_id),
    enabled: thread.origin === "fork",
  });
  const transcript = useRef<HTMLDivElement>(null);
  const scroll = useTranscriptScroll(transcript);
  const stopped = ["failed", "cancelled"].includes(run.status);
  // A stopped Run is asked again from the dock, while it is still the
  // Thread's latest: its message is prefilled there to be sent again.
  const [resubmit, setResubmit] = useState<Resubmission | null>(null);
  const [submitting, setSubmitting] = useState<PendingMessage | null>(null);
  useEffect(() => {
    if (submitting?.runId && runIds.includes(submitting.runId))
      setSubmitting(null);
  }, [submitting, runIds]);
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
    <div className={styles.run} ref={transcript}>
      <ErrorNotice error={error} />
      <BranchOrigin
        thread={thread}
        threads={threads.data ?? [thread]}
        level={level}
      />
      {level === "debug" && <RunNavigator thread={thread} runId={run.id} />}
      <div className={level === "debug" ? debug.sections : styles.transcript}>
        <HistoryTranscript
          runId={runIds[0]!}
          thread={thread}
          level={level}
          preservePosition={scroll.preservePosition}
          jumpToDock={scroll.jumpToLatest}
        />
        {runIds.map((id) => (
          <RunEntry
            key={id}
            runId={id}
            thread={thread}
            level={level}
            followed={id === run.id}
            jumpToDock={scroll.jumpToLatest}
            prefill={id === run.id ? prefill : undefined}
          />
        ))}
        {submitting && !runIds.includes(submitting.runId ?? "") && (
          <div aria-busy="true">
            <UserMessage
              request={{
                kind: "message",
                input: submitting.payload,
                text: inputText(submitting.payload),
              }}
            />
          </div>
        )}
      </div>
      <RunDock
        run={run}
        thread={thread}
        level={level}
        agentName={agentName}
        resubmit={resubmit}
        pendingRunId={submitting?.runId}
        onResubmitted={() => setResubmit(null)}
        onSubmissionChange={(message) => {
          setSubmitting(message);
          if (message && !message.runId) scroll.followLatest();
        }}
        above={
          scroll.showJump && (
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
