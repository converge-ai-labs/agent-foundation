import { memo, useMemo, useState } from "react";
import { ArrowClockwise, CaretDown, CaretRight } from "@phosphor-icons/react";
import { ToolActivity } from "./tool-call";
import {
  activityKind,
  MAX_ACTIVITY_TOOLS,
  savedToolGroups,
  type ToolView,
} from "./tool-presentation";
import { MessageText } from "./message-text";
import { ContextActivity } from "./context-activity";
export { MessageText } from "./message-text";
import type { Schema } from "../transport/client";
import type { DisplayBlock, FocusDisplay } from "./stream";
import { InputContent, type InputPart } from "./input-content";
import { inputSource, type LocalInput } from "./local-input";
import styles from "./conversation.module.css";

function systemNotice(metadata?: Record<string, unknown> | null) {
  return (
    metadata?.["a13n.steering-source"] === "background_process" ||
    metadata?.["a13n.steering-source"] === "async_subagent"
  );
}

type Row = {
  id: string;
  anchor?: string;
  position?: number;
  readingAnchor?: string;
} & (
  | { kind: "input"; parts: InputPart[]; status?: string }
  | { kind: "thinking"; segments: { id: string; text: string }[] }
  | { kind: "tools"; tools: ToolView[] }
  | {
      kind: "assistant";
      text: string;
      target?: Schema<"SavedOutputTarget"> | null;
      truncated?: boolean;
      live?: boolean;
    }
  | {
      kind: "context";
      context: "handoff" | "compaction";
      text: string;
      status?: string;
    }
  | { kind: "activity"; name: string; text: string }
);

function appendInput(rows: Row[], part: InputPart, fallback: string) {
  const id = `input:${inputSource(part) ?? fallback}`;
  const previous = rows.at(-1);
  if (previous?.kind === "input" && previous.id === id)
    previous.parts.push(part);
  else rows.push({ id, kind: "input", parts: [part] });
}

// A continuation changes comment targets, not the identity of unchanged content.
// Include the actual entry content so history replacement cannot reuse an unrelated
// row merely because it occupies the same numeric position.
export function savedEntryIdentity(entry: Schema<"TranscriptEntry">) {
  return JSON.stringify([
    entry.position,
    entry.timestamp,
    entry.message_kind,
    entry.parts.map(({ comment_target: _target, ...part }) => part),
  ]);
}

function savedRows(
  entries: Schema<"TranscriptEntry">[],
  groups: Map<Schema<"TranscriptPart">, ToolView[] | null>,
  continuation?: string | null,
): Row[] {
  const rows: Row[] = [];
  for (const entry of entries) {
    const identity = savedEntryIdentity(entry);
    const start = rows.length;
    entry.parts.forEach((part, index) => {
      if (part.metadata?.display === false || part.kind === "system") return;
      const id = `saved:${identity}:${index}`;
      const context = part.metadata?.["a13n.context"];
      if (context === "handoff" || context === "compaction") {
        const operation = part.metadata?.operation_id;
        rows.push({
          id: typeof operation === "string" ? `context:${operation}` : id,
          kind: "context",
          context,
          text: part.text ?? "",
        });
      } else if (systemNotice(part.metadata)) {
        rows.push({
          id,
          kind: "activity",
          name: "System notification",
          text: part.text ?? JSON.stringify(part.value, null, 2),
        });
      } else if (part.kind === "user" || part.kind === "media") {
        appendInput(rows, part, identity);
      } else if (groups.has(part)) {
        const tools = groups.get(part);
        if (tools)
          rows.push({ id: `tools:${tools[0].id}`, kind: "tools", tools });
      } else if (part.kind === "assistant") {
        rows.push({
          id,
          kind: "assistant",
          text: part.text ?? "",
          target: part.comment_target,
          truncated: part.text_truncated,
        });
      } else if (part.kind === "thinking") {
        rows.push({
          id,
          kind: "thinking",
          segments: [{ id, text: part.text ?? "" }],
        });
      } else
        rows.push({
          id,
          kind: "activity",
          name: part.kind,
          text:
            (part.text ?? JSON.stringify(part.value, null, 2)) +
            (part.value_omitted ? "\nContent omitted by the server." : ""),
        });
    });
    for (let index = start; index < rows.length; index++) {
      rows[index].position = entry.position;
      rows[index].readingAnchor = `saved:${entry.position}:${index - start}`;
    }
    if (rows.length > start && continuation)
      rows[start].anchor = `entry:${continuation}:${entry.position}`;
  }
  return rows;
}

function liveRows(blocks: DisplayBlock[]): Row[] {
  const rows: Row[] = [];
  for (const block of blocks) {
    if (
      block.diagnostic ||
      block.kind === "task" ||
      block.metadata?.display === false
    )
      continue;
    if (block.context) {
      rows.push({
        id: block.id,
        kind: "context",
        context: block.context,
        text: block.result ?? "",
        status: block.text,
      });
    } else if (systemNotice(block.metadata)) {
      rows.push({
        id: block.id,
        kind: "activity",
        name: "System notification",
        text: block.text,
      });
    } else if (block.kind === "user" || block.kind === "media") {
      const turn = block.id.includes(":input:")
        ? block.id.slice(0, block.id.lastIndexOf(":"))
        : block.id;
      appendInput(rows, block, turn);
    } else if (block.kind === "tool") {
      rows.push({
        id: block.id,
        kind: "tools",
        tools: [
          {
            ...block,
            name: block.name || "Tool",
            input: block.text || undefined,
            inputComplete: block.done,
          },
        ],
      });
    } else if (block.kind === "thinking") {
      rows.push({
        id: block.id,
        kind: "thinking",
        segments: [{ id: block.id, text: block.text }],
      });
    } else if (block.kind === "assistant")
      rows.push({
        id: block.id,
        kind: "assistant",
        text: block.text,
        live: true,
      });
    else
      rows.push({
        id: block.id,
        kind: "activity",
        name: block.name || "Activity",
        text: [block.text, block.result].filter(Boolean).join("\n"),
      });
  }
  return rows;
}

// Group only adjacent visible items. Separate Markdown documents remain separate:
// an unfinished fence in one reasoning part cannot consume the next part.
function groupRows(rows: Row[]) {
  const grouped: Row[] = [];
  for (const row of rows) {
    const previous = grouped.at(-1);
    if (row.kind === "thinking" && previous?.kind === "thinking") {
      previous.segments.push(...row.segments);
    } else if (
      row.kind === "tools" &&
      previous?.kind === "tools" &&
      previous.tools.length + row.tools.length <= MAX_ACTIVITY_TOOLS &&
      activityKind(row.tools[0]) &&
      activityKind(row.tools[0]) === activityKind(previous.tools[0])
    ) {
      previous.tools.push(...row.tools);
    } else
      grouped.push(
        row.kind === "thinking"
          ? { ...row, segments: [...row.segments] }
          : row.kind === "tools"
            ? { ...row, tools: [...row.tools] }
            : row,
      );
  }
  return grouped;
}

function Reasoning({ segments }: { segments: { id: string; text: string }[] }) {
  const [open, setOpen] = useState(true);
  return (
    <details
      className={styles.reasoning}
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>Reasoning</summary>
      {segments.map((segment) => (
        <MessageText key={segment.id} text={segment.text} />
      ))}
    </details>
  );
}

function Rows({
  rows,
  threadId,
  continuation = false,
}: {
  rows: Row[];
  threadId?: string;
  continuation?: boolean;
}) {
  return groupRows(rows).map((row, index, all) => (
    <div
      key={row.id}
      data-presence-anchor={row.anchor}
      data-reading-anchor={
        row.kind === "input" ? row.id : (row.readingAnchor ?? row.id)
      }
      data-message-id={row.kind === "input" ? row.id : undefined}
    >
      {row.kind === "input" ? (
        <InputContent
          threadId={threadId}
          parts={row.parts}
          status={row.status}
          renderText={(text) => <MessageText text={text} />}
        />
      ) : row.kind === "thinking" ? (
        <Reasoning segments={row.segments} />
      ) : row.kind === "tools" ? (
        <ToolActivity tools={row.tools} />
      ) : row.kind === "assistant" ? (
        <section
          className={styles.assistantMessage}
          data-saved-target={
            row.target ? JSON.stringify(row.target) : undefined
          }
        >
          {((index === 0 && !continuation) ||
            (index > 0 && all[index - 1].kind === "input")) && (
            <header>Assistant</header>
          )}
          <MessageText text={row.text} />
          {row.truncated && (
            <small>Saved preview truncated by the server.</small>
          )}
        </section>
      ) : row.kind === "context" ? (
        <ContextActivity
          context={row.context}
          text={row.text}
          status={row.status}
        />
      ) : (
        <details className={styles.activity}>
          <summary>{row.name}</summary>
          <pre className={styles.code}>{row.text}</pre>
        </details>
      )}
    </div>
  ));
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
  return (
    <article className={styles.entry} data-position={entry.position}>
      <Rows
        rows={savedRows([entry], toolGroups ?? savedToolGroups([entry]))}
        threadId={threadId}
        continuation={continuation}
      />
    </article>
  );
});

export function ConversationTranscript({
  entries,
  blocks,
  localInputs,
  continuation,
  threadId,
  gap = false,
  turns = [],
  loadEarlier,
  loadingEarlier = false,
}: {
  turns?: Schema<"TranscriptTurn">[];
  loadEarlier?: () => void;
  loadingEarlier?: boolean;
  entries: Schema<"TranscriptEntry">[];
  blocks: DisplayBlock[];
  localInputs: LocalInput[];
  continuation?: string | null;
  threadId: string;
  gap?: boolean;
}) {
  const saved = useMemo(
    () => savedRows(entries, savedToolGroups(entries), continuation),
    [entries, continuation],
  );
  const live = liveRows(blocks);
  const savedIds = new Set(
    saved.filter((row) => row.kind === "input").map((row) => row.id),
  );
  const local = new Map(
    localInputs.map((input) => [`input:${input.id}`, input]),
  );
  const observed = new Set([...savedIds]);
  const rows = [
    ...saved,
    ...live
      .filter((row) => row.kind !== "input" || !savedIds.has(row.id))
      .map((row): Row => {
        if (row.kind !== "input") return row;
        observed.add(row.id);
        const input = local.get(row.id);
        // A streamed input can arrive in several frames. Keep the complete authored
        // preview until saved history supplies the authoritative expanded parts.
        return input ? { ...row, parts: input.parts } : row;
      }),
  ];
  for (const [id, input] of local) {
    // Preparation and unconfirmed submissions belong in the composer, not the
    // transcript. Server-observed input above remains authoritative even if its
    // HTTP acknowledgement is delayed or lost.
    if (input.state === "accepted" && !observed.has(id))
      rows.push({
        id,
        kind: "input",
        parts: input.parts,
      });
  }
  return (
    <>
      <TurnRows
        rows={rows}
        entries={entries}
        turns={turns}
        localInputs={localInputs}
        threadId={threadId}
        loadEarlier={loadEarlier}
        loadingEarlier={loadingEarlier}
      />
      {gap && <GapNotice />}
    </>
  );
}

function TurnRows({
  rows,
  entries,
  turns,
  localInputs,
  threadId,
  loadEarlier,
  loadingEarlier,
}: {
  rows: Row[];
  entries: Schema<"TranscriptEntry">[];
  turns: Schema<"TranscriptTurn">[];
  localInputs: LocalInput[];
  threadId: string;
  loadEarlier?: () => void;
  loadingEarlier: boolean;
}) {
  const groups: { id: string; turn?: Schema<"TranscriptTurn">; rows: Row[] }[] =
    [];
  for (const row of rows) {
    const turn =
      row.position === undefined
        ? undefined
        : turns.find(
            (candidate) =>
              row.position! >= candidate.input_position &&
              row.position! < candidate.end_position,
          );
    const local =
      row.kind === "input"
        ? localInputs.find(
            (input) =>
              `input:${input.id}` === row.id && input.action === "send",
          )
        : undefined;
    const id = turn?.turn_id ?? local?.id ?? groups.at(-1)?.id ?? "ungrouped";
    if (groups.at(-1)?.id !== id) groups.push({ id, turn, rows: [] });
    groups.at(-1)!.rows.push(row);
  }
  return groups.map((group) =>
    group.id === "ungrouped" ? (
      <Rows key={group.id} rows={group.rows} threadId={threadId} />
    ) : (
      <Turn
        key={group.id}
        {...group}
        threadId={threadId}
        missing={
          !!group.turn &&
          entries.filter(
            (entry) =>
              entry.position >= group.turn!.input_position &&
              entry.position < group.turn!.end_position,
          ).length <
            group.turn.end_position - group.turn.input_position
        }
        loadEarlier={loadEarlier}
        loadingEarlier={loadingEarlier}
      />
    ),
  );
}

function Turn({
  id,
  turn,
  rows,
  threadId,
  missing,
  loadEarlier,
  loadingEarlier,
}: {
  id: string;
  turn?: Schema<"TranscriptTurn">;
  rows: Row[];
  threadId: string;
  missing: boolean;
  loadEarlier?: () => void;
  loadingEarlier: boolean;
}) {
  const [expanded, setExpanded] = useState<boolean>();
  const complete =
    turn?.final_position != null &&
    rows.every((row) => row.position !== undefined);
  const input = rows.filter(
    (row) =>
      row.kind === "input" &&
      (turn ? row.position === turn.input_position : row.id === `input:${id}`),
  );
  const final = complete
    ? rows.filter(
        (row) =>
          row.kind === "assistant" && row.position === turn.final_position,
      )
    : [];
  const process = rows.filter(
    (row) => !input.includes(row) && !final.includes(row),
  );
  const open = expanded ?? !complete;
  return (
    <section
      data-turn-id={id}
      data-incomplete-turn={missing ? "" : undefined}
      className={styles.turn}
      tabIndex={-1}
    >
      <Rows rows={input} threadId={threadId} />
      {(process.length > 0 || missing) && (
        <div className={styles.execution}>
          <button
            type="button"
            aria-expanded={open}
            className={styles.executionToggle}
            onClick={() => setExpanded(!open)}
          >
            {open ? (
              <CaretDown aria-hidden="true" />
            ) : (
              <CaretRight aria-hidden="true" />
            )}
            Execution details
            {!!turn?.tool_count && (
              <span>
                {" "}
                · {turn.tool_count}{" "}
                {turn.tool_count === 1 ? "tool call" : "tool calls"}
              </span>
            )}
            {!!turn?.steering_count && (
              <span>
                {" "}
                · {turn.steering_count}{" "}
                {turn.steering_count === 1
                  ? "steering message"
                  : "steering messages"}
              </span>
            )}
          </button>
          <div hidden={!open} className={styles.executionContent}>
            {missing && (
              <button
                type="button"
                className={styles.executionToggle}
                onClick={loadEarlier}
                disabled={!loadEarlier || loadingEarlier}
              >
                {loadingEarlier
                  ? "Loading earlier steps…"
                  : "Load earlier steps"}
              </button>
            )}
            <Rows rows={process} threadId={threadId} continuation />
          </div>
        </div>
      )}
      <Rows rows={final} threadId={threadId} continuation />
    </section>
  );
}

export function RecoveryNotice({
  recovery,
}: {
  recovery: FocusDisplay["recovery"];
}) {
  if (!recovery) return null;
  return (
    <div
      role="status"
      className={styles.recoveryNotice}
      data-state={recovery.state}
    >
      <ArrowClockwise aria-hidden="true" />
      <span>
        {recovery.state === "retrying"
          ? "Reconnecting to model…"
          : recovery.state === "resumed"
            ? "Model connection restored"
            : "Model reconnection ended"}
        {` · ${recovery.retries} ${recovery.retries === 1 ? "retry" : "retries"}`}
      </span>
    </div>
  );
}

function GapNotice() {
  return (
    <p role="status">
      Some live content is unavailable. Saved history and execution details
      remain authoritative.
    </p>
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
  const rows = liveRows(blocks);
  return (
    <section className={styles.liveOutput} aria-label="Current unsaved output">
      {rows.length > 0 && label && <small>{label}</small>}
      <Rows rows={rows} threadId={threadId} />
      {gap && <GapNotice />}
    </section>
  );
}
