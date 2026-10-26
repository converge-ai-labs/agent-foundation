import { Button } from "a13n-ui";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import { ArrowDownIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import type { Schema } from "../../../shared/api";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import { useAgent } from "../../agents/queries";
import { conversationQueries, runPath, type ViewLevel } from "../api";
import { useRun, useSession } from "../queries";
import { runResubmission, type Resubmission } from "../resubmit";
import { BranchOrigin } from "../thread-branches";
import { RunNavigator } from "./debug/run-navigator";
import { DebugPendingRun } from "./debug/run-section";
import { useViewLevel } from "./debug/view";
import { HistoryTranscript } from "./history";
import { RunEntry } from "./run-entry";
import { RunDock, ThreadDock, type PendingMessage } from "./run-dock";
import { useInteractiveThread } from "./run-actions";
import { useTranscriptScroll } from "./use-transcript-scroll";
import { UserMessage } from "./user-message";
import { inputText } from "../input";
import type { RunRequest } from "../request";
import debug from "./debug/debug.module.css";
import styles from "./transcript.module.css";

export function RunPage() {
  const { runId = "", threadId = "", sessionId = "" } = useParams();
  const { state } = useLocation();
  const [shown, setShown] = useState([runId]);
  let runIds = shown;
  if (shown.at(-1) !== runId) {
    // Submissions and followed successors continue this view. History links and Back open the
    // requested run on its own, with that run's authoritative lineage.
    runIds =
      state?.continuedFrom === shown.at(-1) && !shown.includes(runId)
        ? [...shown, ...(state?.continuedRunIds ?? [runId])]
        : [runId];
    setShown(runIds);
  }
  return (
    <RunContent
      key={threadId}
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
    { workspace, basePath } = useWorkspace(),
    queries = conversationQueries(client, workspace.id);
  const navigate = useNavigate();
  const { search } = useLocation();
  const following = new URLSearchParams(search).get("follow") === "latest";
  const runQuery = useRun(runId);
  const items = useQuery({ ...queries.items(runId), enabled: !!runId });
  const [retained, setRetained] = useState<{
    run: Schema["RunView"];
    runIds: string[];
  } | null>(null);
  const threadQuery = useQuery({
    ...queries.thread(threadId),
    enabled: !!threadId,
  });
  const session = useSession(sessionId);
  const run = runQuery.data;
  const thread = threadQuery.data;
  const valid = run?.thread_id === threadId && run.session_id === sessionId;
  const ready = valid && !!items.data;
  const presentation = ready ? { run, runIds } : retained;
  const switching = !ready;
  const agent = useAgent(presentation?.run.agent_id);
  // Keep the last complete presentation until both bounded reads are ready.
  // Query keys own the data: a late response for another selection cannot win.
  useEffect(() => {
    if (ready) setRetained({ run, runIds });
    // The sequence, not the caller's array allocation, identifies this view.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, run, runIds.join(":")]);
  const { level } = useViewLevel(thread, session.data);
  const latest = thread?.current_run_id ?? thread?.last_run_id;
  const successor = useRun(following && latest !== runId ? latest : undefined);
  const indirect = !!successor.data && successor.data.parent_run_id !== runId;
  const lineage = useInfiniteQuery({
    ...queries.lineage(latest ?? ""),
    enabled: following && latest !== runId && indirect,
  });
  useEffect(() => {
    // Load before changing routes so the existing transcript, draft and inspectors
    // remain mounted throughout an automatic successor transition.
    if (
      !following ||
      !successor.data ||
      successor.data.id !== latest ||
      latest === runId ||
      successor.data.thread_id !== threadId ||
      successor.data.session_id !== sessionId
    )
      return;
    let continuedRunIds = [latest];
    if (indirect) {
      if (!lineage.data || lineage.isError) return;
      const entries = lineage.data.pages.flatMap((page) => page.items);
      const anchor = entries.findIndex((entry) => entry.id === runId);
      if (anchor < 0 && lineage.hasNextPage) {
        if (!lineage.isFetchingNextPage) void lineage.fetchNextPage();
        return;
      }
      // Reconnects and enabling follow on history can skip several successors.
      // Include the entire interval, not just the newest Run.
      continuedRunIds =
        anchor < 0
          ? []
          : entries
              .slice(0, anchor)
              .map((entry) => entry.id)
              .reverse();
    }
    navigate(
      runPath(basePath, {
        session_id: sessionId,
        thread_id: threadId,
        run_id: latest,
      }) + search,
      {
        replace: true,
        state: continuedRunIds.length
          ? { continuedFrom: runId, continuedRunIds }
          : null,
      },
    );
  }, [
    following,
    successor.data,
    indirect,
    lineage.data,
    lineage.isError,
    lineage.hasNextPage,
    lineage.isFetchingNextPage,
    lineage.fetchNextPage,
    latest,
    runId,
    threadId,
    sessionId,
    basePath,
    search,
    navigate,
  ]);

  // The navigator belongs to the Thread. Keep it mounted while the selected
  // Run loads; only its transcript and composer start a new inspection lifetime.
  if (threadQuery.isPending || session.isPending)
    return <Loading variant="detail" />;
  if (!thread)
    return (
      <ErrorNotice
        error={threadQuery.error}
        retry={() => void threadQuery.refetch()}
      />
    );
  const debugLevel = level === "debug";
  return (
    <div className={debugLevel ? debug.page : styles.page}>
      <div className={debugLevel ? debug.frame : styles.page}>
        {debugLevel && <RunNavigator thread={thread} runId={runId} />}
        {presentation ? (
          <RunBody
            run={presentation.run}
            runIds={presentation.runIds}
            thread={thread}
            level={level}
            agentName={agent.data?.name}
            switching={switching}
            pending={
              switching &&
              !runQuery.isError &&
              !items.isError &&
              (!run || valid)
            }
            retry={() => {
              void runQuery.refetch();
              void items.refetch();
            }}
            error={
              run && !valid
                ? new Error(t("This run does not belong to this thread."))
                : (runQuery.error ??
                  items.error ??
                  threadQuery.error ??
                  successor.error ??
                  lineage.error)
            }
          />
        ) : (
          <div className={styles.opening}>
            {runQuery.isError || items.isError || (run && !valid) ? (
              <ErrorNotice
                error={
                  run && !valid
                    ? new Error(t("This run does not belong to this thread."))
                    : (runQuery.error ?? items.error)
                }
                retry={() => {
                  void runQuery.refetch();
                  void items.refetch();
                }}
              />
            ) : (
              <p role="status">{t("Loading run…")}</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function RunBody({
  run,
  runIds,
  thread,
  level,
  agentName,
  switching,
  pending,
  retry,
  error,
}: {
  run: Schema["RunView"];
  runIds: string[];
  thread: Schema["ThreadView"];
  level: ViewLevel;
  agentName?: string;
  switching: boolean;
  pending: boolean;
  retry: () => void;
  error: unknown;
}) {
  const { t } = useTranslation();
  const { can, workspace } = useWorkspace();
  const interactive = useInteractiveThread(thread);
  const client = useClient();
  const threads = useQuery({
    ...conversationQueries(client, workspace.id).threads(thread.session_id),
    enabled: thread.origin === "fork",
  });
  const transcript = useRef<HTMLDivElement>(null);
  const scroll = useTranscriptScroll(transcript, level, runIds[0]);
  const stopped = ["failed", "cancelled"].includes(run.status);
  // A stopped Run is asked again from the dock, while it is still the
  // Thread's latest: its message is prefilled there to be sent again.
  const [resubmit, setResubmit] = useState<Resubmission | null>(null);
  const [submitting, setSubmitting] = useState<PendingMessage | null>(null);
  const [anchor, setAnchor] = useState(runIds[0]);
  if (anchor !== runIds[0]) {
    setAnchor(runIds[0]);
    setResubmit(null);
    setSubmitting(null);
  }
  useEffect(() => {
    if (submitting?.runId && runIds.includes(submitting.runId))
      setSubmitting(null);
  }, [submitting, runIds]);
  const resubmission =
    stopped &&
    interactive &&
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
  const debugLevel = level === "debug";
  // Debug is a tool page: the Thread's run index beside its logs. Chat's
  // wrappers add no box, and both levels keep them so a switch keeps the dock
  // and its draft.
  return (
    <div
      className={debugLevel ? `${styles.run} ${debug.main}` : styles.run}
      ref={transcript}
    >
      {(pending || error != null) && (
        <div className={styles.switchStatus}>
          {pending && <p role="status">{t("Loading run…")}</p>}
          <ErrorNotice error={error} retry={retry} />
        </div>
      )}
      <div
        key={runIds[0]}
        inert={switching}
        aria-busy={pending}
        className={styles.observation}
      >
        <BranchOrigin
          thread={thread}
          threads={threads.data ?? [thread]}
          level={level}
        />
        <div className={debugLevel ? debug.sections : styles.transcript}>
          {level === "chat" && (
            <HistoryTranscript
              runId={runIds[0]!}
              thread={thread}
              level={level}
              preservePosition={scroll.preservePosition}
              jumpToDock={scroll.jumpToLatest}
            />
          )}
          {runIds.map((id) => (
            <RunEntry
              key={id}
              runId={id}
              thread={thread}
              level={level}
              jumpToDock={scroll.jumpToLatest}
              preservePosition={scroll.preservePosition}
              prefill={id === run.id ? prefill : undefined}
            />
          ))}
          {submitting &&
            !runIds.includes(submitting.runId ?? "") &&
            (debugLevel ? (
              <DebugPendingRun request={pendingRequest(submitting)} />
            ) : (
              <div aria-busy="true">
                <UserMessage request={pendingRequest(submitting)} />
              </div>
            ))}
        </div>
      </div>
      <ThreadDock thread={thread}>
        <div inert={switching} className={styles.dockRun}>
          <RunDock
            key={runIds[0]}
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
      </ThreadDock>
    </div>
  );
}

/** A message sent from the dock, read as the request it will become. */
function pendingRequest(message: PendingMessage): RunRequest {
  return {
    kind: "message",
    input: message.payload,
    text: inputText(message.payload),
  };
}
