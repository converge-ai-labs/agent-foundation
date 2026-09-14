import { memo } from "react";
import { ToolActivity } from "./tool-call";
import {
  activityKind,
  MAX_ACTIVITY_TOOLS,
  savedToolGroups,
  type ToolView,
} from "./tool-presentation";
import { MessageText } from "./message-text";
import { SavedOutput } from "./comments";
export { MessageText } from "./message-text";
import type { Schema } from "../transport/client";
import type { DisplayBlock } from "./stream";
import { InputContent } from "./input-content";
import styles from "./conversation.module.css";

export const SavedEntry = memo(function SavedEntry({
  entry,
  threadId,
  toolGroups,
}: {
  toolGroups?: Map<Schema<"TranscriptPart">, ToolView[] | null>;
  entry: Schema<"TranscriptEntry">;
  threadId?: string;
}) {
  const parts = entry.parts.filter((part) => part.metadata?.display !== false);
  if (!parts.length) return null;
  const tools = toolGroups ?? savedToolGroups([entry]);
  if (parts.every((part) => tools.get(part) === null)) return null;
  return (
    <article className={styles.entry} data-position={entry.position}>
      {parts.some((part) => part.kind === "user" || part.kind === "media") && (
        <InputContent
          threadId={threadId}
          parts={parts.filter(
            (part) => part.kind === "user" || part.kind === "media",
          )}
          renderText={(text) => <MessageText text={text} />}
        />
      )}
      {parts.map((part, index) => {
        if (part.kind === "user" || part.kind === "media") return null;
        if (tools.has(part)) {
          const tool = tools.get(part);
          return tool ? <ToolActivity key={index} tools={tool} /> : null;
        }
        if (part.kind === "assistant")
          return (
            <section
              key={index}
              className={styles.assistantMessage}
              data-saved-target={
                part.comment_target
                  ? JSON.stringify(part.comment_target)
                  : undefined
              }
            >
              <header>Assistant</header>
              <SavedOutput
                target={part.comment_target}
                text={part.text ?? ""}
                truncated={part.text_truncated}
              />
            </section>
          );
        return (
          <details key={index} className={styles.activity}>
            <summary>
              {part.kind === "thinking"
                ? "Reasoning"
                : part.kind === "system"
                  ? "System context"
                  : part.kind}
            </summary>
            <pre className={styles.code}>
              {part.text ?? JSON.stringify(part.value, null, 2)}
              {part.value_omitted ? "\nContent omitted by the server." : ""}
            </pre>
          </details>
        );
      })}
    </article>
  );
});
export function LiveOutput({
  blocks,
  gap,
  threadId,
}: {
  blocks: DisplayBlock[];
  gap: boolean;
  threadId?: string;
}) {
  const diagnostics = blocks.filter((block) => block.diagnostic);
  const items: (DisplayBlock | DisplayBlock[] | { tools: ToolView[] })[] = [];
  for (const block of blocks.filter((block) => !block.diagnostic)) {
    if (block.kind === "user" || block.kind === "media") {
      const previous = items.at(-1);
      const turn = (id: string) =>
        id.includes(":input:") ? id.slice(0, id.lastIndexOf(":")) : id;
      if (Array.isArray(previous) && turn(previous[0].id) === turn(block.id))
        previous.push(block);
      else items.push([block]);
    } else if (block.kind === "tool") {
      const tool: ToolView = {
        ...block,
        name: block.name || "Tool",
        input: block.text || undefined,
        inputComplete: block.done,
      };
      const previous = items.at(-1);
      const kind = activityKind(tool);
      if (
        previous &&
        !Array.isArray(previous) &&
        "tools" in previous &&
        previous.tools.length < MAX_ACTIVITY_TOOLS &&
        kind &&
        activityKind(previous.tools[0]) === kind
      )
        previous.tools.push(tool);
      else items.push({ tools: [tool] });
    } else items.push(block);
  }
  return (
    <section className={styles.liveOutput} aria-label="Current unsaved output">
      {blocks.length > 0 && (
        <small>Current output · not yet established as saved history</small>
      )}
      {items.map((block) =>
        Array.isArray(block) ? (
          <InputContent
            key={block[0].id}
            threadId={threadId}
            parts={block}
            renderText={(text) => <MessageText text={text} />}
          />
        ) : "tools" in block ? (
          <ToolActivity key={block.tools[0].id} tools={block.tools} />
        ) : block.kind === "task" ? (
          <div key={block.id} className={styles.activity}>
            <strong>{block.text}</strong>
            <small>
              {block.name} {block.result}
            </small>
          </div>
        ) : block.kind === "assistant" ? (
          <div key={block.id} className={styles.assistantMessage}>
            <header>Assistant</header>
            <MessageText text={block.text} />
          </div>
        ) : (
          <details key={block.id} className={styles.activity}>
            <summary>
              {block.kind === "thinking"
                ? "Reasoning"
                : block.name || "Activity"}
            </summary>
            {block.text && <pre className={styles.code}>{block.text}</pre>}
            {block.result && <MessageText text={block.result} />}
          </details>
        ),
      )}
      {!!diagnostics.length && (
        <details className={styles.activity}>
          <summary>Stream diagnostics · {diagnostics.length} events</summary>
          {diagnostics.map((block) => (
            <details key={block.id}>
              <summary>{block.name}</summary>
              <pre className={styles.code}>{block.text}</pre>
            </details>
          ))}
        </details>
      )}
      {gap && (
        <p role="status">
          Some live content is unavailable. Saved history and execution details
          remain authoritative.
        </p>
      )}
    </section>
  );
}
