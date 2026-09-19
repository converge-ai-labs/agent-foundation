import type { ReactNode } from "react";
import type { Schema } from "../../../shared/api";
import { MarkdownContent } from "../../../shared/markdown";
import type { PresentedItem } from "../projection";
import { AgentTurn } from "./assistant-message";
import { RunSeparator } from "./run-separator";
import { UserMessage } from "./user-message";

/**
 * One assembly renders every run in the transcript, whether it is the run being
 * followed or an ancestor loaded above it.
 */
export function RunBlock({
  run,
  items,
  agentName,
  agentImageUrl,
  separatorAction,
  earlier,
  children,
}: {
  run: Schema["RunResource"];
  items: readonly PresentedItem[];
  agentName?: string;
  agentImageUrl?: string | null;
  /** A link or control shown inside the run separator. */
  separatorAction?: ReactNode;
  /** The "load earlier messages" control for this run's own items. */
  earlier?: ReactNode;
  children?: ReactNode;
}) {
  const spoken = items.some(
    (item) =>
      item.kind === "text_message" && item.role === "assistant" && item.text,
  );
  return (
    <>
      <RunSeparator
        createdAt={run.created_at}
        state={run.status}
        action={separatorAction}
      />
      <UserMessage
        input={run.input}
        fallback={run.input_text}
        kind={run.input_kind}
      />
      {earlier}
      <AgentTurn
        items={items}
        runState={run.status}
        agentName={agentName}
        agentId={run.agent_id}
        agentImageUrl={agentImageUrl}
      >
        {!spoken && run.output_text && (
          <MarkdownContent text={run.output_text} />
        )}
        {children}
      </AgentTurn>
    </>
  );
}
