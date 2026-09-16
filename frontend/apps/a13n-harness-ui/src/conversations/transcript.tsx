import { memo, useState } from "react";
import { Button } from "a13n-ui";
import { X } from "@phosphor-icons/react";
import type { ThreadDraft } from "./draft";
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

function systemNotice(metadata?: Record<string, unknown> | null) {
  return (
    metadata?.["a13n.steering-source"] === "background_process" ||
    metadata?.["a13n.steering-source"] === "async_subagent"
  );
}

function Reasoning({ text }: { text: string }) {
  const [open, setOpen] = useState(true);
  return (
    <details
      className={styles.reasoning}
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>Reasoning</summary>
      <MessageText text={text} />
    </details>
  );
}

export const SavedEntry = memo(function SavedEntry({
  entry,
  threadId,
  toolGroups,
  continuation = false,
}: {
  continuation?: boolean;
  toolGroups?: Map<Schema<"TranscriptPart">, ToolView[] | null>;
  entry: Schema<"TranscriptEntry">;
  threadId?: string;
}) {
  const parts = entry.parts.filter(
    (part) => part.metadata?.display !== false && part.kind !== "system",
  );
  const input = parts.filter(
    (part) =>
      (part.kind === "user" || part.kind === "media") &&
      !systemNotice(part.metadata),
  );
  if (!parts.length) return null;
  const tools = toolGroups ?? savedToolGroups([entry]);
  if (parts.every((part) => tools.get(part) === null)) return null;
  return (
    <article className={styles.entry} data-position={entry.position}>
      {!!input.length && (
        <InputContent
          threadId={threadId}
          parts={input}
          renderText={(text) => <MessageText text={text} />}
        />
      )}
      {parts.map((part, index) => {
        if (systemNotice(part.metadata))
          return (
            <details key={index} className={styles.activity}>
              <summary>System notification</summary>
              <pre className={styles.code}>
                {part.text ?? JSON.stringify(part.value, null, 2)}
              </pre>
            </details>
          );
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
              {!continuation &&
                index ===
                  parts.findIndex((item) => item.kind === "assistant") && (
                  <header>Assistant</header>
                )}
              <SavedOutput
                target={part.comment_target}
                text={part.text ?? ""}
                truncated={part.text_truncated}
              />
            </section>
          );
        if (part.kind === "thinking")
          return <Reasoning key={index} text={part.text ?? ""} />;
        return (
          <details key={index} className={styles.activity}>
            <summary>{part.kind}</summary>
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
export function SteerNotice({ draft }: { draft: ThreadDraft }) {
  if (
    draft.submission.kind !== "accepted" ||
    draft.submission.action !== "steer"
  )
    return null;
  return (
    <div role="status" className={styles.steerNotice}>
      <span>{draft.submission.message}</span>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label="Dismiss steer notification"
        onClick={() => {
          draft.submission = { kind: "idle" };
          draft.notify();
        }}
      >
        <X />
      </Button>
    </div>
  );
}

export function LiveOutput({
  blocks,
  gap,
  threadId,
  label,
}: {
  label?: string;
  blocks: DisplayBlock[];
  gap: boolean;
  threadId?: string;
}) {
  const items: (DisplayBlock | DisplayBlock[] | { tools: ToolView[] })[] = [];
  for (const original of blocks.filter(
    (block) => !block.diagnostic && block.kind !== "task",
  )) {
    const block: DisplayBlock = systemNotice(original.metadata)
      ? { ...original, kind: "activity", name: "System notification" }
      : original;
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
      {items.length > 0 && label && <small>{label}</small>}
      {items.map((block, index) =>
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
            {(index === 0 || Array.isArray(items[index - 1])) && (
              <header>Assistant</header>
            )}
            <MessageText text={block.text} />
          </div>
        ) : block.kind === "thinking" ? (
          <Reasoning key={block.id} text={block.text} />
        ) : (
          <details key={block.id} className={styles.activity}>
            <summary>{block.name || "Activity"}</summary>
            {block.text && <pre className={styles.code}>{block.text}</pre>}
            {block.result && <MessageText text={block.result} />}
          </details>
        ),
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
