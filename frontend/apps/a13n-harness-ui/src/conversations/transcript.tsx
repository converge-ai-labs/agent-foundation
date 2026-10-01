import {
  memo,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useTurnHistory } from "./queries";
import {
  captureReadingAnchor,
  restoreReadingAnchor,
  type ReadingAnchor,
} from "./reading-anchor";
import { ArrowClockwise } from "@phosphor-icons/react";
import { ExecutionDetails } from "./execution-details";
import { AppCard } from "../mcp-apps/app-card";
import { QuestionInteraction, ToolActivity } from "./tool-call";
import {
  activityKind,
  describeTool,
  MAX_ACTIVITY_TOOLS,
  savedToolGroups,
  type ToolView,
} from "./tool-presentation";
import { MessageText } from "./message-text";
import { CopyMessage } from "./copy-message";
import { ContextActivity } from "./context-activity";
export { MessageText } from "./message-text";
import type { Schema } from "../transport/client";
import type { DisplayBlock, FocusDisplay } from "./stream";
import { InputContent, type InputPart } from "./input-content";
import { inputSource, type LocalInput } from "./local-input";
import styles from "./conversation.module.css";

function systemNotice(metadata?: Record<string, unknown> | null) {
  const source = metadata?.["a13n.steering-source"];
  return source === "background_process" || source === "async_subagent"
    ? source
    : undefined;
}

function notificationTitle(source: string) {
  return source === "background_process" ? "Process update" : "Subagent update";
}

type PendingInteraction = { requestIds: string[]; content: ReactNode };

type Row = {
  id: string;
  subagentRunId?: string;
  anchor?: string;
  position?: number;
  readingAnchor?: string;
} & (
  | { kind: "input"; parts: InputPart[]; status?: string }
  | { kind: "thinking"; segments: { id: string; text: string }[] }
  | { kind: "tools"; tools: ToolView[] }
  | { kind: "origin"; childId: string }
  | { kind: "app"; reference: Schema<"AppReference">; live?: boolean }
  | { kind: "question"; tool?: ToolView; content?: ReactNode }
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
  | {
      kind: "notifications";
      notices: { id: string; source: string; text: string }[];
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
      // Sibling parts can grow at later checkpoints without replacing this part.
      const id = `saved:${savedEntryIdentity({ ...entry, parts: [part] })}:${index}`;
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
          kind: "notifications",
          notices: [
            {
              id,
              source: systemNotice(part.metadata)!,
              text: part.text ?? JSON.stringify(part.value, null, 2),
            },
          ],
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
      for (const reference of part.mcp_apps ?? []) {
        rows.push({ id: reference.app_id, kind: "app", reference });
      }
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
  let previousChild: string | undefined;
  for (const block of blocks) {
    if (
      block.diagnostic ||
      block.kind === "task" ||
      block.metadata?.display === false
    )
      continue;
    const firstRow = rows.length;
    if (block.subagentRunId && block.subagentRunId !== previousChild) {
      rows.push({
        id: `${block.id}:origin`,
        kind: "origin",
        childId: block.subagentRunId,
      });
    }
    previousChild = block.subagentRunId;
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
        kind: "notifications",
        notices: [
          {
            id: block.id,
            source: systemNotice(block.metadata)!,
            text: block.text,
          },
        ],
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
    for (const reference of block.apps ?? []) {
      rows.push({ id: reference.app_id, kind: "app", reference, live: true });
    }
    for (let index = firstRow; index < rows.length; index++)
      rows[index].subagentRunId = block.subagentRunId;
  }
  return rows;
}

function questionKey(tool: ToolView) {
  const scope = tool.subagentRunId ? `child:${tool.subagentRunId}:` : "";
  return `question:${scope}${tool.provider ?? "function"}:${tool.toolCallId}`;
}

// Resume runs can contain result-only blocks. Correlate within the native Turn,
// by call identity, and keep the interaction at the original call's position.
function questionRows(rows: Row[], pending?: PendingInteraction): Row[] {
  const questions = new Map<string, Extract<Row, { kind: "question" }>>();
  const projected: Row[] = [];
  for (const row of rows) {
    if (row.kind !== "tools") {
      projected.push(row);
      continue;
    }
    if (
      !row.tools.some(
        (tool) =>
          tool.name === "ask_user_question" ||
          (tool.toolCallId && questions.has(questionKey(tool))),
      )
    ) {
      projected.push(row);
      continue;
    }
    for (const tool of row.tools) {
      const key = tool.toolCallId ? questionKey(tool) : undefined;
      const previous = key ? questions.get(key) : undefined;
      if (previous) {
        // A duplicate call/partial frame must not erase an observed result.
        if (tool.result !== undefined || tool.resultOmitted || tool.outcome)
          previous.tool = {
            ...previous.tool!,
            ...tool,
            id: previous.tool!.id,
            name: previous.tool!.name,
            input: tool.input || previous.tool!.input,
          };
      } else if (
        tool.name === "ask_user_question" &&
        (!tool.provider || tool.provider === "function")
      ) {
        const question: Extract<Row, { kind: "question" }> = {
          ...row,
          id: key ?? row.id,
          kind: "question",
          tool,
        };
        projected.push(question);
        if (key) questions.set(key, question);
      } else projected.push({ ...row, id: `tools:${tool.id}`, tools: [tool] });
    }
  }
  if (!pending) return projected;
  const matching = projected.filter(
    (row) =>
      row.kind === "question" &&
      !row.subagentRunId &&
      row.tool?.toolCallId &&
      pending.requestIds.includes(row.tool.toolCallId),
  );
  const anchor = matching[0];
  if (anchor?.kind === "question") anchor.content = pending.content;
  else
    projected.push({
      id: pending.requestIds.length
        ? `question:function:${pending.requestIds[0]}`
        : "pending-decisions",
      kind: "question",
      content: pending.content,
    });
  // Mixed batches keep one form and one atomic submission. Other pending
  // questions already appear inside that form; completed receipts remain distinct.
  return projected.filter((row) => row === anchor || !matching.includes(row));
}

// Group only adjacent visible items. Separate Markdown documents remain separate:
// an unfinished fence in one reasoning part cannot consume the next part.
function groupRows(rows: Row[]) {
  const grouped: Row[] = [];
  for (const row of rows) {
    const last = grouped.at(-1);
    const previous =
      last?.subagentRunId === row.subagentRunId ? last : undefined;
    if (row.kind === "notifications" && previous?.kind === "notifications") {
      previous.notices.push(...row.notices);
    } else if (row.kind === "thinking" && previous?.kind === "thinking") {
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
            : row.kind === "notifications"
              ? { ...row, notices: [...row.notices] }
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
  output = [],
}: {
  rows: Row[];
  threadId?: string;
  continuation?: boolean;
  output?: Extract<Row, { kind: "assistant" }>[];
}) {
  const copyText = output
    .map((row) => row.text)
    .filter(Boolean)
    .join("\n\n");
  return groupRows(rows).map((row, index, all) => (
    <div
      key={row.id}
      data-presence-anchor={row.anchor}
      data-reading-anchor={
        row.kind === "input" ? row.id : (row.readingAnchor ?? row.id)
      }
      data-message-id={row.kind === "input" ? row.id : undefined}
      data-subagent-run-id={row.subagentRunId}
    >
      {row.kind === "input" ? (
        <InputContent
          threadId={threadId}
          parts={row.parts}
          status={row.status}
          renderText={(text) => <MessageText text={text} />}
        />
      ) : row.kind === "origin" ? (
        <div className={styles.assistantMessage}>
          <header>
            Subagent output <small>{row.childId}</small>
          </header>
        </div>
      ) : row.kind === "thinking" ? (
        <Reasoning segments={row.segments} />
      ) : row.kind === "question" ? (
        (row.content ?? (row.tool && <QuestionInteraction tool={row.tool} />))
      ) : row.kind === "app" ? (
        <AppCard reference={row.reference} live={row.live} />
      ) : row.kind === "tools" ? (
        <ToolActivity tools={row.tools} />
      ) : row.kind === "assistant" ? (
        <section
          className={styles.assistantMessage}
          data-saved-target={
            row.target ? JSON.stringify(row.target) : undefined
          }
        >
          {!row.subagentRunId &&
            ((index === 0 && !continuation) ||
              (index > 0 &&
                (all[index - 1].kind === "input" ||
                  all[index - 1].subagentRunId))) && <header>Assistant</header>}
          <MessageText text={row.text} />
          {row.truncated && (
            <small>Saved preview truncated by the server.</small>
          )}
          {row === output.at(-1) && copyText.trim() && (
            <CopyMessage
              text={copyText}
              truncated={output.some((part) => part.truncated)}
            />
          )}
        </section>
      ) : row.kind === "context" ? (
        <ContextActivity
          context={row.context}
          text={row.text}
          status={row.status}
        />
      ) : row.kind === "notifications" ? (
        <details className={styles.activity}>
          <summary>
            {row.notices.length === 1
              ? notificationTitle(row.notices[0].source)
              : `System updates · ${row.notices.length}`}
          </summary>
          {row.notices.map((notice) => (
            <div key={notice.id}>
              {row.notices.length > 1 && (
                <small>{notificationTitle(notice.source)}</small>
              )}
              <pre className={styles.code}>{notice.text}</pre>
            </div>
          ))}
        </details>
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
  loadDetails = false,
  onSavedEntries,
  recovery,
  pending,
  activity,
}: {
  activity?: ReactNode;
  pending?: PendingInteraction;
  recovery?: FocusDisplay["recovery"];
  turns?: Schema<"TranscriptTurn">[];
  loadDetails?: boolean;
  onSavedEntries?: (entries: Schema<"TranscriptEntry">[]) => void;
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
        loadDetails={loadDetails}
        onSavedEntries={onSavedEntries}
        continuation={continuation}
        recovery={recovery}
        pending={pending}
      />
      {activity}
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
  loadDetails,
  onSavedEntries,
  continuation,
  recovery,
  pending,
}: {
  pending?: PendingInteraction;
  recovery?: FocusDisplay["recovery"];
  rows: Row[];
  entries: Schema<"TranscriptEntry">[];
  turns: Schema<"TranscriptTurn">[];
  localInputs: LocalInput[];
  threadId: string;
  loadDetails: boolean;
  onSavedEntries?: (entries: Schema<"TranscriptEntry">[]) => void;
  continuation?: string | null;
}) {
  const indexed = useMemo(() => {
    const ordered = [...turns].sort(
      (a, b) => a.input_position - b.input_position,
    );
    const byPosition = new Map<number, Schema<"TranscriptTurn">>();
    const byTurn = new Map<string, Schema<"TranscriptEntry">[]>();
    for (const turn of ordered) byTurn.set(turn.turn_id, []);
    for (const entry of entries) {
      let low = 0;
      let high = ordered.length;
      while (low < high) {
        const middle = (low + high) >>> 1;
        if (ordered[middle].input_position <= entry.position) low = middle + 1;
        else high = middle;
      }
      const turn = ordered[low - 1];
      if (turn && entry.position < turn.end_position) {
        byPosition.set(entry.position, turn);
        byTurn.get(turn.turn_id)!.push(entry);
      }
    }
    return { byPosition, byTurn };
  }, [entries, turns]);
  const localSends = new Map(
    localInputs
      .filter((input) => input.action === "send")
      .map((input) => [`input:${input.id}`, input]),
  );
  const groups: { id: string; turn?: Schema<"TranscriptTurn">; rows: Row[] }[] =
    [];
  for (const row of rows) {
    const turn =
      row.position === undefined
        ? undefined
        : indexed.byPosition.get(row.position);
    const local = row.kind === "input" ? localSends.get(row.id) : undefined;
    const id = turn?.turn_id ?? local?.id ?? groups.at(-1)?.id ?? "ungrouped";
    if (groups.at(-1)?.id !== id) groups.push({ id, turn, rows: [] });
    groups.at(-1)!.rows.push(row);
  }
  if (!groups.length && (recovery || pending))
    groups.push({ id: "ungrouped", rows: [] });
  return groups.map((group, index) =>
    group.id === "ungrouped" && !recovery && !pending ? (
      <Rows
        key={group.id}
        rows={questionRows(group.rows)}
        threadId={threadId}
      />
    ) : (
      <Turn
        key={group.id}
        {...group}
        recovery={index === groups.length - 1 ? recovery : undefined}
        pending={index === groups.length - 1 ? pending : undefined}
        entries={group.turn ? indexed.byTurn.get(group.turn.turn_id)! : entries}
        threadId={threadId}
        missing={
          !!group.turn &&
          indexed.byTurn.get(group.turn.turn_id)!.length <
            group.turn.end_position - group.turn.input_position
        }
        loadDetails={loadDetails}
        onSavedEntries={onSavedEntries}
        continuation={continuation}
      />
    ),
  );
}

function Turn(props: {
  pending?: PendingInteraction;
  recovery?: FocusDisplay["recovery"];
  id: string;
  turn?: Schema<"TranscriptTurn">;
  rows: Row[];
  threadId: string;
  missing: boolean;
  entries: Schema<"TranscriptEntry">[];
  loadDetails: boolean;
  onSavedEntries?: (entries: Schema<"TranscriptEntry">[]) => void;
  continuation?: string | null;
}) {
  return props.loadDetails ? (
    <TurnHistory {...props} />
  ) : (
    <TurnSegments {...props} />
  );
}

function TurnHistory({
  turn,
  entries,
  rows,
  continuation,
  onSavedEntries,
  ...props
}: {
  pending?: PendingInteraction;
  id: string;
  recovery?: FocusDisplay["recovery"];
  turn?: Schema<"TranscriptTurn">;
  entries: Schema<"TranscriptEntry">[];
  rows: Row[];
  threadId: string;
  missing: boolean;
  continuation?: string | null;
  onSavedEntries?: (entries: Schema<"TranscriptEntry">[]) => void;
}) {
  const history = useTurnHistory(
    props.threadId,
    continuation,
    turn,
    props.missing,
  );
  useEffect(() => {
    if (history.data) onSavedEntries?.(history.data);
  }, [history.data, onSavedEntries]);
  const loaded = useMemo(() => {
    const byPosition = new Map<number, Schema<"TranscriptEntry">>();
    for (const entry of [...entries, ...(history.data ?? [])]) {
      if (
        turn &&
        entry.position >= turn.input_position &&
        entry.position < turn.end_position
      )
        byPosition.set(entry.position, entry);
    }
    return [...byPosition.values()].sort((a, b) => a.position - b.position);
  }, [entries, history.data, turn]);
  const saved = useMemo(
    () => savedRows(loaded, savedToolGroups(loaded), continuation),
    [loaded, continuation],
  );
  const savedInputs = new Set(
    saved.filter((row) => row.kind === "input").map((row) => row.id),
  );
  const merged = [
    ...saved,
    ...rows.filter(
      (row) => row.position === undefined && !savedInputs.has(row.id),
    ),
  ];
  const retry = () => {
    if (!history.isFetching) void history.refetch();
  };
  return (
    <TurnSegments
      {...props}
      turn={turn}
      rows={turn ? merged : rows}
      entries={turn ? loaded : entries}
      missing={
        !!turn && loaded.length < turn.end_position - turn.input_position
      }
      retry={retry}
      loading={history.isFetching}
      failed={history.isError}
    />
  );
}

// Presentation identity is separate from source identity. Match only the same
// text slot within an input boundary when live text becomes saved verbatim.
// Never deduplicate by text or carry state from changed saved history. The new
// row (including its exact comment target) always remains authoritative.
function usePresentedRows(rows: Row[], complete: boolean) {
  const previous = useRef(new Map<string, { source: Row; presented: Row }>());
  const next = new Map<string, { source: Row; presented: Row }>();
  const sourceIds = new Set(rows.map((row) => row.id));
  let boundary = "turn";
  const ordinals = new Map<string, number>();
  const presented = rows.map((row) => {
    if (row.kind === "input") boundary = row.id;
    const group = `${boundary}:${row.subagentRunId ?? "root"}:${row.kind}`;
    const ordinal = ordinals.get(group) ?? 0;
    ordinals.set(group, ordinal + 1);
    const slot = `${group}:${ordinal}`;
    const old = previous.current.get(slot);
    const text = (value: Row) =>
      value.kind === "assistant"
        ? value.text
        : value.kind === "thinking"
          ? value.segments.map((segment) => segment.text).join("\n")
          : undefined;
    const cutover =
      complete &&
      old &&
      !sourceIds.has(old.source.id) &&
      old.source.position === undefined &&
      row.position !== undefined &&
      text(row) !== undefined &&
      text(row) === text(old.source);
    const retained = old && (old.source.id === row.id || cutover);
    const result = retained
      ? {
          ...row,
          id: old.presented.id,
          readingAnchor: old.presented.readingAnchor ?? old.presented.id,
          ...(row.kind === "thinking" && old.presented.kind === "thinking"
            ? {
                segments: row.segments.map((segment, index) => ({
                  ...segment,
                  id:
                    old.presented.kind === "thinking"
                      ? (old.presented.segments[index]?.id ?? segment.id)
                      : segment.id,
                })),
              }
            : {}),
        }
      : row;
    next.set(slot, { source: row, presented: result });
    return result;
  });
  useLayoutEffect(() => {
    previous.current = next;
  });
  return presented;
}

function TurnSegments({
  id,
  turn,
  rows: sourceRows,
  entries,
  threadId,
  missing,
  retry,
  loading = false,
  failed = false,
  recovery,
  pending,
}: {
  pending?: PendingInteraction;
  recovery?: FocusDisplay["recovery"];
  id: string;
  turn?: Schema<"TranscriptTurn">;
  rows: Row[];
  entries: Schema<"TranscriptEntry">[];
  threadId: string;
  missing: boolean;
  retry?: () => void;
  loading?: boolean;
  failed?: boolean;
}) {
  const rows = usePresentedRows(questionRows(sourceRows, pending), !missing);
  const mountedExecution = useRef(new Set<string>());
  const complete =
    turn?.final_position != null &&
    rows.every((row) => row.position !== undefined);
  const outputPosition = turn?.output_position ?? turn?.final_position;
  const output = rows.flatMap((row) =>
    outputPosition != null &&
    row.kind === "assistant" &&
    row.position === outputPosition
      ? [row]
      : [],
  );
  const positions = new Set(entries.map((entry) => entry.position));
  const segments: {
    id: string;
    kind: "visible" | "execution" | "gap";
    rows: Row[];
  }[] = [];
  let previousPosition = turn?.input_position ?? 0;
  // Key execution by its preceding conversation boundary, not transient live
  // row IDs, so saving the same stretch does not close an open reader.
  let boundary = id;
  let textIndex = 0;
  const appendGap = (before: number) => {
    if (missing && before > previousPosition) {
      let gap = false;
      for (let position = previousPosition; position < before; position++) {
        if (!positions.has(position)) {
          gap = true;
          break;
        }
      }
      if (gap) {
        boundary = `gap:${before}`;
        textIndex = 0;
        segments.push({ id: boundary, kind: "gap", rows: [] });
      }
    }
    previousPosition = before;
  };
  for (const row of rows) {
    appendGap(row.position ?? turn?.end_position ?? previousPosition);
    const kind =
      !row.subagentRunId &&
      (row.kind === "input" ||
        row.kind === "assistant" ||
        row.kind === "app" ||
        row.kind === "question")
        ? "visible"
        : "execution";
    if (row.kind === "input") {
      boundary = row.id;
      textIndex = 0;
    } else if (row.kind === "question" || row.kind === "app") {
      boundary = row.id;
      textIndex = 0;
    } else if (row.kind === "assistant") textIndex++;
    const previous = segments.at(-1);
    if (
      previous?.kind === kind &&
      (kind === "execution" ||
        (row.kind !== "question" &&
          row.kind !== "app" &&
          previous.rows.at(-1)?.kind !== "question" &&
          previous.rows.at(-1)?.kind !== "app"))
    )
      previous.rows.push(row);
    else
      segments.push({
        id:
          kind === "execution" ? `${boundary}:execution:${textIndex}` : row.id,
        kind,
        rows: [row],
      });
  }
  appendGap(turn?.end_position ?? previousPosition);
  // Wait for the whole saved turn before introducing execution segments, but
  // keep mounted readers through live-to-saved cutover so inspection stays open.
  const visibleSegments = segments.filter(
    (segment) =>
      segment.kind !== "execution" ||
      !missing ||
      !retry ||
      mountedExecution.current.has(segment.id) ||
      segment.rows.some((row) => row.position === undefined),
  );
  let recoverySegment = visibleSegments.findLast(
    (segment) => segment.kind === "execution",
  );
  if (recovery && !recoverySegment) {
    // Use the next execution boundary so arriving tools reuse an open reader.
    recoverySegment = {
      id: `${boundary}:execution:${textIndex}`,
      kind: "execution",
      rows: [],
    };
    visibleSegments.push(recoverySegment);
  }
  useLayoutEffect(() => {
    mountedExecution.current = new Set(
      visibleSegments
        .filter((segment) => segment.kind === "execution")
        .map((segment) => segment.id),
    );
  });
  return (
    <section data-turn-id={id} className={styles.turn} tabIndex={-1}>
      {visibleSegments.map((segment) => {
        if (segment.kind === "gap")
          return (
            <div
              key={segment.id}
              className={styles.executionLoad}
              role="status"
            >
              {loading ? (
                "Loading turn…"
              ) : failed && retry ? (
                <>
                  Turn could not be fully loaded.{" "}
                  <button type="button" onClick={retry}>
                    Retry loading turn
                  </button>
                </>
              ) : (
                "Turn history is incomplete."
              )}
            </div>
          );
        if (segment.kind === "visible")
          return (
            <Rows
              key={segment.id}
              rows={segment.rows}
              threadId={threadId}
              output={output}
            />
          );
        return (
          <ExecutionSegment
            key={segment.id}
            rows={segment.rows}
            threadId={threadId}
            complete={complete}
            recovery={segment === recoverySegment ? recovery : undefined}
          />
        );
      })}
    </section>
  );
}

function ExecutionSegment({
  rows,
  threadId,
  complete,
  recovery,
}: {
  recovery?: FocusDisplay["recovery"];
  rows: Row[];
  threadId: string;
  complete: boolean;
}) {
  const latest = rows.at(-1);
  const latestTool = latest?.kind === "tools" ? latest.tools.at(-1) : undefined;
  const toolInfo = latestTool ? describeTool(latestTool) : undefined;
  const toolCount = rows.reduce(
    (count, row) => count + (row.kind === "tools" ? row.tools.length : 0),
    0,
  );
  const runningTools = rows
    .flatMap((row) => (row.kind === "tools" ? row.tools : []))
    .filter((tool) =>
      ["Receiving input", "Awaiting result"].includes(describeTool(tool).phase),
    ).length;
  const preview = toolInfo
    ? `${toolInfo.label} ${toolInfo.summary} · ${toolInfo.phase}`
    : latest?.kind === "thinking"
      ? "Reasoning…"
      : "View the available execution steps";
  return (
    <ExecutionDetails
      complete={complete}
      preview={preview}
      summary={
        toolCount ? (
          <span>
            {" "}
            · {toolCount} {toolCount === 1 ? "tool call" : "tool calls"}
            {runningTools > 0 && ` · ${runningTools} running`}
          </span>
        ) : null
      }
    >
      {(open) => (
        <ExecutionReader open={open}>
          <Rows rows={rows} threadId={threadId} continuation />
          <RecoveryNotice recovery={recovery} />
        </ExecutionReader>
      )}
    </ExecutionDetails>
  );
}

function ExecutionReader({
  open,
  children,
}: {
  open: boolean;
  children: ReactNode;
}) {
  const reader = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const anchor = useRef<ReadingAnchor | undefined>(undefined);
  useLayoutEffect(() => {
    const element = reader.current;
    if (!open || !element) return;
    if (follow.current) element.scrollTop = element.scrollHeight;
    else restoreReadingAnchor(element, anchor.current);
    anchor.current = captureReadingAnchor(element);
  }, [children, open]);
  return (
    <div
      ref={reader}
      hidden={!open}
      role="region"
      aria-label="Execution details"
      tabIndex={0}
      data-execution-reader
      className={`${styles.executionContent} a13n-scrollbar`}
      onWheel={(event) => {
        event.stopPropagation();
        if (event.deltaY < 0) follow.current = false;
      }}
      onTouchStart={(event) => {
        event.stopPropagation();
        follow.current = false;
      }}
      onPointerDown={(event) => {
        event.stopPropagation();
        follow.current = false;
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") return;
        event.stopPropagation();
        if (["ArrowUp", "PageUp", "Home"].includes(event.key))
          follow.current = false;
      }}
      onScroll={(event) => {
        if (event.target !== event.currentTarget) return;
        const element = event.currentTarget;
        follow.current =
          element.scrollHeight - element.scrollTop - element.clientHeight <= 1;
        anchor.current = captureReadingAnchor(element);
      }}
    >
      {children}
    </div>
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
