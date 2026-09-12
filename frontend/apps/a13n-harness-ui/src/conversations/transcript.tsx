import { memo } from "react";
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
}: {
  entry: Schema<"TranscriptEntry">;
  threadId?: string;
}) {
  const parts = entry.parts.filter((part) => part.metadata?.display !== false);
  if (!parts.length) return null;
  const tools = parts.filter(
    (part) => part.kind === "tool_call" || part.kind === "tool_result",
  );
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
        if (part.kind === "tool_call" || part.kind === "tool_result")
          return null;
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
      {!!tools.length && (
        <details className={styles.activity}>
          <summary>
            Tool activity ·{" "}
            {tools.filter((tool) => tool.kind === "tool_call").length} calls
          </summary>
          {tools.map((tool, index) => (
            <details key={`${tool.tool_call_id}:${index}`}>
              <summary>
                {tool.tool_name || "Tool"} ·{" "}
                {tool.kind === "tool_call" ? "Arguments" : "Result"}
              </summary>
              <pre className={styles.code}>
                {tool.text ?? JSON.stringify(tool.value, null, 2)}
                {tool.value_omitted ? "\nContent omitted by the server." : ""}
              </pre>
            </details>
          ))}
        </details>
      )}
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
  const tools = blocks.filter((block) => block.kind === "tool");
  const diagnostics = blocks.filter((block) => block.diagnostic);
  const items: (DisplayBlock | DisplayBlock[])[] = [];
  for (const block of blocks.filter(
    (block) => block.kind !== "tool" && !block.diagnostic,
  )) {
    if (block.kind === "user" || block.kind === "media") {
      const previous = items.at(-1);
      const turn = (id: string) =>
        id.includes(":input:") ? id.slice(0, id.lastIndexOf(":")) : id;
      if (Array.isArray(previous) && turn(previous[0].id) === turn(block.id))
        previous.push(block);
      else items.push([block]);
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
      {!!tools.length && (
        <details className={styles.activity}>
          <summary>Tool activity · {tools.length} calls</summary>
          {tools.map((tool) => (
            <details key={tool.id}>
              <summary>
                {tool.name || "Tool"} ·{" "}
                {tool.result !== undefined
                  ? "Result received"
                  : tool.done
                    ? "Arguments complete"
                    : "Running"}
              </summary>
              <pre className={styles.code}>{tool.text}</pre>
              {tool.result !== undefined && (
                <pre className={styles.code}>{tool.result}</pre>
              )}
            </details>
          ))}
        </details>
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
