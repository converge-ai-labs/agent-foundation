import type { ReactNode } from "react";
import { useWorkspace } from "../../../layout/workspace";
import type { Schema } from "../../../shared/api";
import { MarkdownContent } from "../../../shared/markdown";
import { runRequest } from "../request";
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
  run: Schema["RunResource"];
  /** The Thread that owns the run; a child Thread reads a delegated task. */
  thread: Schema["ThreadResource"];
  /** The same reading of the run the Debug level renders. */
  timeline: RunTimeline;
  agentName?: string;
  agentImageUrl?: string | null;
  /** A quiet link for this turn, such as "View run" on an ancestor. */
  separatorAction?: ReactNode;
  /** The "load earlier messages" control for this run's own items. */
  earlier?: ReactNode;
  children?: ReactNode;
}) {
  const { basePath } = useWorkspace();
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
      {separatorAction && (
        <div className={styles.turnAction}>{separatorAction}</div>
      )}
      <UserMessage request={runRequest(run, thread)} />
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
        {!spoken && run.output_text && (
          <MarkdownContent text={run.output_text} />
        )}
        {children}
      </AgentTurn>
    </article>
  );
}
