import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { GitBranchIcon } from "@phosphor-icons/react";
import { useWorkspace } from "../../../layout/workspace";
import type { Schema } from "../../../shared/api";
import { MarkdownContent } from "../../../shared/markdown";
import { runRequest } from "../request";
import { ForkRun } from "../fork-run";
import type { RunTimeline } from "../timeline";
import { AgentTurn } from "./assistant-message";
import { hasDelegation, transcriptBlocks } from "./items";
import { childThreadOf, childThreadPath, useChildThreads } from "./thread-runs";
import { UserMessage } from "./user-message";
import styles from "./transcript.module.css";

/**
 * One assembly renders every run in the transcript, whether it is the run being
 * followed or an ancestor loaded above it. Runs are separated by space alone:
 * the page reads as one conversation, not as a stack of labelled cards.
 */
export function RunBlock({
  run,
  thread,
  timeline,
  agentName,
  agentImageUrl,
  separatorAction,
  earlier,
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
  separatorAction?: ReactNode;
  /** What the transcript cannot show of this run's own earlier Items. */
  earlier?: ReactNode;
  children?: ReactNode;
}) {
  const { basePath } = useWorkspace();
  const { t } = useTranslation();
  const blocks = transcriptBlocks(timeline.entries, run.status);
  // Only a run that delegated has a child thread to resolve; nothing else asks
  // the session for its threads.
  const threads = useChildThreads(
    hasDelegation(blocks) ? run.session_id : "",
    run.thread_id,
  );
  const child = childThreadOf(threads, run.id);
  const spoken = blocks.some(
    (block) => block.kind === "message" && !!block.entry.text,
  );
  return (
    <article className={styles.turn} data-run-id={run.id}>
      {run.lineage === "fork" && (
        <div className={styles.branchBoundary}>
          <GitBranchIcon size={14} aria-hidden="true" />
          {t("New branch starts here")}
        </div>
      )}
      {separatorAction && (
        <div className={styles.turnAction}>{separatorAction}</div>
      )}
      <UserMessage
        request={runRequest(run, thread)}
        entryId={run.source_entry_id}
        principalId={run.principal_id}
      />
      {earlier}
      <AgentTurn
        blocks={blocks}
        runState={run.status}
        durationMs={timeline.totals.durationMs}
        agentName={agentName}
        agentId={run.agent_id}
        agentImageUrl={agentImageUrl}
        childPath={child && childThreadPath(basePath, child)}
      >
        {!spoken && typeof run.output === "string" && run.output && (
          <MarkdownContent text={run.output} />
        )}
        {children}
        <div className={styles.runActions}>
          <ForkRun run={run} thread={thread} level="chat" />
        </div>
      </AgentTurn>
    </article>
  );
}
