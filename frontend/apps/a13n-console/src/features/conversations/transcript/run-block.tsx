import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { GitBranchIcon } from "@phosphor-icons/react";
import { useWorkspace } from "../../../layout/workspace";
import type { Schema } from "../../../shared/api";
import { MarkdownContent } from "../../../shared/markdown";
import { isActiveRun } from "../api";
import { runRequest } from "../request";
import { ForkRun } from "../fork-run";
import { RunModelButton } from "../run-agent";
import type { RunTimeline } from "../timeline";
import { AgentTurn, CopyReply } from "./assistant-message";
import { hasDelegation, transcriptBlocks } from "./items";
import { childThreadOf, childThreadPath, useChildThreads } from "./thread-runs";
import { UserMessage } from "./user-message";
import styles from "./transcript.module.css";

/**
 * One assembly renders every run in the transcript, whether it is the run being
 * followed or an ancestor loaded above it. Runs are separated by space alone:
 * the page reads as one conversation, not as a stack of labelled cards, and a
 * finished answer closes with its quiet actions.
 */
export function RunBlock({
  run,
  thread,
  timeline,
  agentName,
  agentImageUrl,
  action,
  children,
}: {
  run: Schema["RunView"];
  /** The Thread that owns the run; a child Thread reads a delegated task. */
  thread: Schema["ThreadView"];
  /** The same reading of the run the Debug level renders. */
  timeline: RunTimeline;
  agentName?: string;
  agentImageUrl?: string | null;
  /** A quiet link for this turn, such as "View run" on an ancestor. */
  action?: ReactNode;
  children?: ReactNode;
}) {
  const { basePath, can } = useWorkspace();
  const { t } = useTranslation();
  const blocks = transcriptBlocks(timeline.entries, run.status);
  // Only a run that delegated has a child thread to resolve; nothing else asks
  // the session for its threads.
  const threads = useChildThreads(
    hasDelegation(blocks) ? run.session_id : "",
    run.thread_id,
  );
  const child = childThreadOf(threads, run.id);
  const replies = blocks.flatMap((block) =>
    block.kind === "message" && block.entry.text ? [block.entry] : [],
  );
  const reply = replies.at(-1);
  // Actions belong to a finished answer. A working run has none to offer yet,
  // and a run waiting for the person offers only its answer below the turn.
  const finished =
    !!run.sealed_at && !isActiveRun(run.status) && run.status !== "waiting";
  const branch = finished && can("run");
  const copy = finished && reply?.state === "completed" ? reply : null;
  return (
    <article className={styles.turn} data-run-id={run.id}>
      {run.lineage === "fork" && (
        <div className={styles.branchBoundary}>
          <GitBranchIcon size={14} aria-hidden="true" />
          {t("New branch starts here")}
        </div>
      )}
      <UserMessage
        request={runRequest(run, thread)}
        entryId={run.source_entry_id}
        principalId={run.principal_id}
      />
      <AgentTurn
        blocks={blocks}
        durationMs={
          run.status === "completed" ? timeline.totals.durationMs : null
        }
        agentName={agentName}
        agentId={run.agent_id}
        agentImageUrl={agentImageUrl}
        model={<RunModelButton run={run} />}
        childPath={child && childThreadPath(basePath, child)}
        footer={
          (copy || branch || action) && (
            <div className={styles.turnActions}>
              {copy && <CopyReply entry={copy} />}
              {branch && <ForkRun run={run} thread={thread} level="chat" />}
              {action}
            </div>
          )
        }
      >
        {!reply && typeof run.output === "string" && run.output && (
          <MarkdownContent text={run.output} />
        )}
        {children}
      </AgentTurn>
    </article>
  );
}
