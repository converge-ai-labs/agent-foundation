import {
  ExecutionNoticeLinks,
  useNoticeLabel,
  splitExecutionNotices,
  executionHeaders,
} from "./background-links";
import { ConnectorOffer } from "../connectors/offers";
import { useTranslation } from "../i18n";
import { BOT_THREAD_ID, botInputId } from "../bot-identity";
import {
  memo,
  Fragment,
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ScrollArea } from "a13n-ui";
import { useTurnHistory } from "./queries";
import {
  captureReadingAnchor,
  restoreReadingAnchor,
  type ReadingAnchor,
} from "./reading-anchor";
import { ArrowClockwise, CaretRight } from "@phosphor-icons/react";
import { executionTime } from "./execution-time";
import { ExecutionDetails } from "./execution-details";
import { AppCard } from "../mcp-apps/app-card";
import { QuestionInteraction, ToolActivity } from "./tool-call";
import {
  toolLabel,
  toolSummary,
  activityKind,
  describeTool,
  MAX_ACTIVITY_TOOLS,
  savedToolGroups,
  type ToolView,
} from "./tool-presentation";
import { AnnotatedReply } from "./annotated-reply";
import { MessageText } from "./message-text";
import { MessageActionButton, MessageBranches } from "./message-branch";
import { RecoveryText } from "./run-activity";
import { CopyMessage } from "./copy-message";
import { ContextActivity } from "./context-activity";
import { FailureNotice } from "./failure-notice";

const TerminalTurn = createContext<
  { id: string; time: string; notice?: ReactNode } | undefined
>(undefined);
const CurrentFailureRun = createContext<string | null | undefined>(undefined);
export { MessageText } from "./message-text";
import type { Schema } from "../transport/client";
import type {
  ChildState,
  ContextState,
  DisplayBlock,
  ExecutionOutcome,
  FocusDisplay,
} from "./stream";
import { InputContent, type InputPart } from "./input-content";
import { inputSource, type LocalInput } from "./local-input";
import { DebugData } from "../shell/debug";
import styles from "./conversation.module.css";

function systemNotice(metadata?: Record<string, unknown> | null) {
  const source = metadata?.["a13n.steering-source"];
  return source === "background_process" ||
    source === "async_subagent" ||
    source === "tool_execution"
    ? source
    : undefined;
}

// Generated run, subagent and recovery chrome is translated here; backend
// messages, part kinds and outcome values stay verbatim.
function ActivityRow({ row }: { row: Extract<Row, { kind: "activity" }> }) {
  const { t } = useTranslation();
  const title = row.execution
    ? row.execution === "cancelled"
      ? t("Execution cancelled")
      : row.execution === "interrupt"
        ? t("Execution suspended")
        : t("Execution failed")
    : row.childName
      ? t("Subagent - {{name}}", { name: row.childName })
      : (row.name ?? t("Activity"));
  const generated =
    row.childState === "running"
      ? t("Running")
      : row.childState === "suspended"
        ? t("Suspended")
        : row.childState === "completed"
          ? t("Completed")
          : row.execution === "interrupt"
            ? t("A response is needed before execution can continue.")
            : row.execution
              ? t(
                  "Run finished. Open its details to check follow-ups and environment results.",
                )
              : "";
  const body = [
    row.text || generated,
    row.recoveryOutcome &&
      t("Outcome: {{outcome}}.", { outcome: row.recoveryOutcome }),
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <details className={styles.activity}>
      <summary>{title}</summary>
      <pre className={styles.code}>
        {row.omitted ? `${body}\n${t("Content omitted by the server.")}` : body}
      </pre>
    </details>
  );
}

type PendingInteraction = { requestIds: string[]; content: ReactNode };

type Row = {
  publication?: boolean;
  displayId?: string;
  timestamp?: number;
  id: string;
  subagentRunId?: string;
  anchor?: string;
  position?: number;
  readingAnchor?: string;
  replySlot?: string;
  savedReplyAnchor?: string;
  turnId?: string;
} & (
  | { kind: "input"; parts: InputPart[]; status?: string }
  | {
      kind: "thinking";
      segments: {
        id: string;
        text: string;
        durationMs?: number;
        running?: boolean;
      }[];
    }
  | { kind: "tools"; tools: ToolView[] }
  | { kind: "origin"; childId: string }
  | { kind: "app"; reference: Schema<"AppReference">; live?: boolean }
  | { kind: "question"; tool?: ToolView; content?: ReactNode }
  | {
      kind: "assistant";
      text: string;
      truncated?: boolean;
      live?: boolean;
    }
  | {
      kind: "context";
      context: "handoff" | "compaction";
      text: string;
      state?: ContextState;
      diagnostics?: string[];
    }
  | {
      kind: "notifications";
      notices: {
        id: string;
        source: string;
        text: string;
        executionIds?: unknown;
      }[];
    }
  | { kind: "failure"; text: string }
  | {
      kind: "activity";
      name?: string;
      text: string;
      omitted?: boolean;
      execution?: ExecutionOutcome;
      recoveryOutcome?: string;
      childName?: string;
      childState?: ChildState;
    }
);

function timestampMilliseconds(value: DisplayBlock["timestamp"]) {
  return typeof value === "string" ? Date.parse(value) : value;
}

function appendInput(
  rows: Row[],
  part: InputPart,
  fallback: string,
  identity?: string,
) {
  const id = `input:${identity ?? inputSource(part) ?? fallback}`;
  const previous = rows.at(-1);
  if (previous?.kind === "input" && previous.id === id)
    previous.parts.push(part);
  else rows.push({ id, kind: "input", parts: [part] });
}

// Include the actual entry content so history replacement cannot reuse an unrelated
// row merely because it occupies the same numeric position.
export function savedEntryIdentity(entry: Schema<"TranscriptEntry">) {
  return JSON.stringify([
    entry.position,
    entry.timestamp,
    entry.message_kind,
    entry.parts,
    entry.failures,
  ]);
}

function savedRows(
  entries: Schema<"TranscriptEntry">[],
  groups: Map<Schema<"TranscriptPart">, ToolView[] | null>,
  continuation?: string | null,
  currentFailureRunId?: string | null,
  threadId?: string,
): Row[] {
  const rows: Row[] = [];
  for (const entry of entries) {
    const identity = savedEntryIdentity(entry);
    const botInput =
      threadId === BOT_THREAD_ID
        ? entry.parts.map((part) => botInputId(part.metadata)).find(Boolean)
        : undefined;
    const start = rows.length;
    entry.parts.forEach((part, index) => {
      if (
        part.metadata?.display === false ||
        part.kind === "system" ||
        (threadId === BOT_THREAD_ID && part.tool_name === "finish_silently")
      )
        return;
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
        splitExecutionNotices(
          part.text ?? JSON.stringify(part.value, null, 2),
        ).forEach((text, index) => {
          rows.push({
            id: `${id}:${index}`,
            kind: "notifications",
            notices: [
              {
                id: `${id}:${index}`,
                source: systemNotice(part.metadata)!,
                executionIds: part.metadata?.execution_ids,
                text,
              },
            ],
          });
        });
      } else if (part.kind === "user" || part.kind === "media") {
        appendInput(
          rows,
          part,
          identity,
          botInput ? `bot:${botInput}` : undefined,
        );
      } else if (groups.has(part)) {
        const tools = groups.get(part);
        if (tools)
          rows.push({ id: `tools:${tools[0].id}`, kind: "tools", tools });
      } else if (part.kind === "assistant") {
        rows.push({
          id,
          kind: "assistant",
          displayId: part.display_id ?? undefined,
          text: part.text ?? "",
          truncated: part.text_truncated,
        });
      } else if (part.kind === "thinking") {
        rows.push({
          id,
          kind: "thinking",
          segments: [
            {
              id,
              text: part.text ?? "",
              durationMs: part.duration_ms ?? undefined,
            },
          ],
        });
      } else
        rows.push({
          id,
          kind: "activity",
          name: part.kind,
          text: part.text ?? JSON.stringify(part.value, null, 2),
          omitted: part.value_omitted ?? undefined,
        });
      for (const reference of part.mcp_apps ?? []) {
        rows.push({ id: reference.app_id, kind: "app", reference });
      }
    });
    for (const failure of entry.failures ?? []) {
      if (currentFailureRunId && failure.run_id === currentFailureRunId)
        continue;
      rows.push({
        id: `failure:${failure.id}`,
        kind: "failure",
        text: failure.message,
      });
    }
    for (let index = start; index < rows.length; index++) {
      rows[index].timestamp = timestampMilliseconds(
        entry.timestamp ?? undefined,
      );
      rows[index].position = entry.position;
      rows[index].readingAnchor = `saved:${entry.position}:${index - start}`;
    }
    if (rows.length > start && continuation)
      rows[start].anchor = `entry:${continuation}:${entry.position}`;
  }
  return rows;
}

function liveRows(
  blocks: DisplayBlock[],
  running = false,
  threadId?: string,
): Row[] {
  const rows: Row[] = [];
  let previousChild: string | undefined;
  const botInputs = new Map(
    blocks.flatMap((block) => {
      const id =
        threadId === BOT_THREAD_ID ? botInputId(block.metadata) : undefined;
      return id && block.inputGroup ? [[block.inputGroup, id] as const] : [];
    }),
  );
  for (const block of blocks) {
    if (
      block.diagnostic ||
      block.kind === "task" ||
      block.metadata?.display === false ||
      (threadId === BOT_THREAD_ID &&
        block.kind === "tool" &&
        block.name === "finish_silently" &&
        !block.subagentRunId)
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
        state: block.contextState,
        diagnostics: block.contextDiagnostics,
      });
    } else if (systemNotice(block.metadata)) {
      splitExecutionNotices(block.text).forEach((text, index) => {
        rows.push({
          id: `${block.id}:${index}`,
          kind: "notifications",
          notices: [
            {
              id: `${block.id}:${index}`,
              source: systemNotice(block.metadata)!,
              executionIds: block.metadata?.execution_ids,
              text,
            },
          ],
        });
      });
    } else if (block.kind === "user" || block.kind === "media") {
      const turn = block.id.includes(":input:")
        ? block.id.slice(0, block.id.lastIndexOf(":"))
        : block.id;
      appendInput(
        rows,
        block,
        block.inputGroup ?? (block.inputSource === "user" ? block.id : turn),
        botInputs.has(block.inputGroup ?? "")
          ? `bot:${botInputs.get(block.inputGroup!)}`
          : undefined,
      );
      if (block.inputSource === "user" && !block.subagentRunId)
        rows.at(-1)!.turnId = block.inputGroup ?? block.id;
    } else if (block.kind === "tool") {
      rows.push({
        id: block.id,
        kind: "tools",
        tools: [
          {
            ...block,
            live: running,
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
        segments: [
          {
            id: block.id,
            text: block.text,
            durationMs: block.durationMs,
            running: running && !block.done,
          },
        ],
      });
    } else if (block.kind === "assistant")
      rows.push({
        id: block.id,
        kind: "assistant",
        displayId: block.id,
        text: block.text,
        live: true,
      });
    else
      rows.push({
        id: block.id,
        kind: "activity",
        name: block.name || undefined,
        text: [block.text, block.result].filter(Boolean).join("\n"),
        execution: block.execution,
        recoveryOutcome: block.recoveryOutcome,
        childName: block.childName,
        childState: block.childState,
      });
    for (const reference of block.apps ?? []) {
      rows.push({ id: reference.app_id, kind: "app", reference, live: true });
    }
    for (let index = firstRow; index < rows.length; index++) {
      rows[index].subagentRunId = block.subagentRunId;
      rows[index].timestamp = timestampMilliseconds(block.timestamp);
    }
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
    if (
      row.kind === "notifications" &&
      previous?.kind === "notifications" &&
      executionHeaders(row.notices[0].text)[0]?.kind ===
        executionHeaders(previous.notices[0].text)[0]?.kind
    ) {
      grouped[grouped.length - 1] = {
        ...previous,
        notices: [...previous.notices, ...row.notices],
      };
    } else if (row.kind === "thinking" && previous?.kind === "thinking") {
      grouped[grouped.length - 1] = {
        ...previous,
        segments: [...previous.segments, ...row.segments],
      };
    } else if (
      row.kind === "tools" &&
      previous?.kind === "tools" &&
      previous.tools.length + row.tools.length <= MAX_ACTIVITY_TOOLS &&
      activityKind(row.tools[0]) &&
      activityKind(row.tools[0]) === activityKind(previous.tools[0])
    ) {
      grouped[grouped.length - 1] = {
        ...previous,
        tools: [...previous.tools, ...row.tools],
      };
    } else grouped.push(row);
  }
  return grouped;
}

function Reasoning({
  segments,
}: {
  segments: {
    id: string;
    text: string;
    durationMs?: number;
    running?: boolean;
  }[];
}) {
  const { t } = useTranslation();
  const running = segments.some((part) => part.running);
  const duration = executionTime(
    segments.every((segment) => segment.durationMs != null)
      ? segments.reduce((total, segment) => total + segment.durationMs!, 0)
      : undefined,
  );
  const [open, setOpen] = useState(false);
  return (
    <details
      className={styles.reasoning}
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary data-running-label={running || undefined}>
        {running ? t("Thinking", { context: "active" }) : t("Thought")}
        {duration && (
          <span className={styles.executionDuration}>
            {t("for {{duration}}", { duration })}
          </span>
        )}
        <CaretRight aria-hidden="true" className={styles.logCaret} />
      </summary>
      <ScrollArea
        autoHeight
        scrollbarGutter
        overscrollContain
        className={`${styles.reasoningBody} ${styles.innerReader}`}
        viewportProps={{
          role: "region",
          "aria-label": t("Thought content"),
          tabIndex: 0,
        }}
      >
        {segments.map((segment) => (
          <MessageText key={segment.id} text={segment.text} />
        ))}
      </ScrollArea>
    </details>
  );
}

function Rows({
  rows,
  threadId,
  continuation = false,
  output = [],
  turn,
  messageActions = false,
}: {
  rows: Row[];
  threadId?: string;
  continuation?: boolean;
  turn?: Schema<"TranscriptTurn">;
  messageActions?: boolean;
  output?: Extract<Row, { kind: "assistant" }>[];
}) {
  const { t } = useTranslation();
  const actions = useContext(MessageBranches);
  const key =
    turn?.turn_id.startsWith("entry:") && turn.timestamp != null
      ? `${turn.turn_id}@${turn.timestamp}`
      : turn?.turn_id;
  const point = key && actions?.points?.[key];
  const notificationTitle = useNoticeLabel();
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
          branchKey={
            messageActions &&
            !row.subagentRunId &&
            row.position === turn?.input_position
              ? (key ?? "")
              : undefined
          }
          threadId={threadId}
          parts={row.parts}
          timestamp={row.timestamp}
          status={row.status}
          renderText={(text) => <MessageText text={text} />}
        />
      ) : row.kind === "failure" ? (
        <FailureNotice message={row.text} />
      ) : row.kind === "origin" ? (
        <div className={styles.assistantMessage}>
          <header>
            {t("Subagent output")} <small>{row.childId}</small>
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
        <section className={styles.assistantMessage}>
          <AnnotatedReply
            source={
              row.savedReplyAnchor ??
              row.replySlot ??
              row.readingAnchor ??
              row.id
            }
            slot={row.replySlot}
          >
            <MessageText text={row.text} />
          </AnnotatedReply>
          {row.truncated && (
            <small>{t("Saved preview truncated by the server.")}</small>
          )}
          {row === output.at(-1) && copyText.trim() && (
            <CopyMessage
              action={
                messageActions &&
                actions &&
                !row.subagentRunId && (
                  <MessageActionButton
                    kind="branch"
                    disabled={
                      !point || !point.branch || actions.pending || !!row.live
                    }
                    onClick={() => {
                      if (key) void actions.create(key);
                    }}
                  />
                )
              }
              timestamp={
                turn?.final_position != null ? row.timestamp : undefined
              }
              text={copyText}
              truncated={output.some((part) => part.truncated)}
            />
          )}
        </section>
      ) : row.kind === "context" ? (
        <ContextActivity
          context={row.context}
          text={row.text}
          state={row.state}
          diagnostics={row.diagnostics}
        />
      ) : row.kind === "notifications" ? (
        <details className={styles.activity}>
          <summary className={styles.noticeSummary}>
            {notificationTitle(
              row.notices[0].text,
              row.notices[0].source,
              row.notices.length,
            )}
            {row.notices.map((notice) => (
              <ExecutionNoticeLinks
                key={notice.id}
                text={notice.text}
                executionIds={
                  notice.source === "async_subagent"
                    ? notice.executionIds
                    : undefined
                }
              />
            ))}
            <CaretRight aria-hidden="true" className={styles.logCaret} />
          </summary>
          {row.notices.map((notice) => (
            <div key={notice.id}>
              {row.notices.length > 1 && (
                <small>{notificationTitle(notice.text, notice.source)}</small>
              )}
              <pre className={styles.code}>{notice.text}</pre>
            </div>
          ))}
        </details>
      ) : (
        <ActivityRow row={row} />
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
  const currentFailureRunId = useContext(CurrentFailureRun);
  const { t } = useTranslation();
  return (
    <article className={styles.entry} data-position={entry.position}>
      <Rows
        rows={savedRows(
          [entry],
          toolGroups ?? savedToolGroups([entry]),
          undefined,
          currentFailureRunId,
          threadId,
        )}
        threadId={threadId}
        continuation={continuation}
      />
      <DebugData
        label={t("Transcript entry {{position}}", { position: entry.position })}
        value={entry}
      />
    </article>
  );
});

export type PublishedMessage = Schema<"BotMessage"> & {
  status?: string;
  local?: boolean;
};

function publicationRow(message: PublishedMessage): Row {
  return message.role === "user"
    ? {
        id: `input:bot:${message.input_id ?? message.message_id.replace(/^user-/, "")}`,
        kind: "input",
        publication: true,
        status: message.status,
        timestamp: Date.parse(message.created_at),
        parts: message.parts?.length
          ? message.parts.map((part) =>
              typeof part === "string"
                ? { kind: "text", text: part }
                : {
                    kind: "attachment",
                    metadata: { harness_ui: { attachment: part } },
                  },
            )
          : [{ kind: "text", text: message.text }],
      }
    : {
        id: message.message_id,
        kind: "assistant",
        publication: true,
        text: message.text,
        timestamp: Date.parse(message.created_at),
      };
}

// Publications fill gaps before native observation and carry proactive messages.
// Correlated native output owns its ordinary timeline position and folds.
function publishedRows(
  native: Row[],
  messages: PublishedMessage[],
  hasEarlier: boolean,
  hasLater: boolean,
): Row[] {
  const rows = [...native];
  const local: Row[] = [];
  const inputIds = new Set(
    native.filter((row) => row.kind === "input").map((row) => row.id),
  );
  const displayIds = new Set(
    native.map((row) => row.displayId).filter(Boolean),
  );
  const oldest = native.find((row) => row.timestamp !== undefined)?.timestamp;
  const newest = native.findLast(
    (row) => row.timestamp !== undefined,
  )?.timestamp;
  for (const message of messages) {
    const row = publicationRow(message);
    if (
      message.role === "user"
        ? inputIds.has(row.id)
        : message.display_ids?.some((id) => displayIds.has(id))
    )
      continue;
    // Publications written before display correlation existed have only their
    // completed reply text. Limit this compatibility join to legacy final replies.
    if (
      message.role === "assistant" &&
      !message.display_ids?.length &&
      message.message_id.startsWith("reply-") &&
      native.some(
        (item) => item.kind === "assistant" && item.text === message.text,
      )
    )
      continue;
    // Local commands have no server timestamp yet. Keep their recovery rows
    // after observed work rather than ordering them by a skewed browser clock.
    if (message.local) {
      if (!hasLater) local.push(row);
      continue;
    }
    if (hasEarlier && (oldest === undefined || row.timestamp! < oldest))
      continue;
    if (hasLater && (newest === undefined || row.timestamp! > newest)) continue;
    const next = rows.findIndex(
      (item) => item.timestamp !== undefined && item.timestamp > row.timestamp!,
    );
    rows.splice(next < 0 ? rows.length : next, 0, row);
  }
  return [...rows, ...local];
}

export function ConversationTranscript({
  connectorOffers = [],
  publications = [],
  hasEarlier = false,
  hasLater = false,
  entries,
  blocks,
  localInputs,
  continuation,
  threadId,
  gap = false,
  turns = [],
  loadDetails = false,
  onSavedEntries,
  pending,
  activity,
  currentFailureRunId,
  terminalTurn,
  running = false,
}: {
  connectorOffers?: Schema<"SetupOffer">[];
  publications?: PublishedMessage[];
  hasEarlier?: boolean;
  hasLater?: boolean;
  terminalTurn?: { id: string; time: string; notice?: ReactNode };
  running?: boolean;
  currentFailureRunId?: string | null;
  activity?: ReactNode;
  pending?: PendingInteraction;
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
    () =>
      savedRows(
        entries,
        savedToolGroups(entries),
        continuation,
        currentFailureRunId,
        threadId,
      ),
    [entries, continuation, currentFailureRunId, threadId],
  );
  const live = liveRows(blocks, running, threadId);
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
    <TerminalTurn value={terminalTurn}>
      <CurrentFailureRun value={currentFailureRunId}>
        <TurnRows
          connectorOffers={connectorOffers}
          rows={
            publications.length
              ? publishedRows(rows, publications, hasEarlier, hasLater)
              : rows
          }
          entries={entries}
          turns={turns}
          localInputs={localInputs}
          threadId={threadId}
          loadDetails={loadDetails}
          onSavedEntries={onSavedEntries}
          continuation={continuation}
          pending={pending}
        />
        {activity}
        {gap && <GapNotice />}
      </CurrentFailureRun>
    </TerminalTurn>
  );
}

function TurnRows({
  connectorOffers,
  rows,
  entries,
  turns,
  localInputs,
  threadId,
  loadDetails,
  onSavedEntries,
  continuation,
  pending,
}: {
  connectorOffers: Schema<"SetupOffer">[];
  pending?: PendingInteraction;
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
  // Composer source identity spans preview -> live -> saved, while native
  // turn identity owns navigation. They need not be the same identifier.
  const presentationKeys = new Map(
    turns.map((turn) => {
      const input = indexed.byTurn
        .get(turn.turn_id)
        ?.find((entry) => entry.position === turn.input_position);
      const source = input?.parts.map(inputSource).find(Boolean);
      return [
        turn.turn_id,
        source ? `input:${source}` : `turn:${turn.turn_id}`,
      ];
    }),
  );
  const groups: {
    id: string;
    presentationKey: string;
    turn?: Schema<"TranscriptTurn">;
    rows: Row[];
  }[] = [];
  for (const row of rows) {
    const turn =
      row.position === undefined
        ? undefined
        : indexed.byPosition.get(row.position);
    const local = row.kind === "input" ? localSends.get(row.id) : undefined;
    const id =
      turn?.turn_id ??
      row.turnId ??
      local?.id ??
      groups.at(-1)?.id ??
      "ungrouped";
    if (groups.at(-1)?.id !== id) {
      const source =
        row.kind === "input"
          ? row.parts.map(inputSource).find(Boolean)
          : undefined;
      const presentationKey = turn
        ? presentationKeys.get(id)!
        : source
          ? `input:${source}`
          : `turn:${id}`;
      groups.push({ id, presentationKey, turn, rows: [] });
    }
    groups.at(-1)!.rows.push(row);
  }
  if (!groups.length && pending)
    groups.push({
      id: "ungrouped",
      presentationKey: "turn:ungrouped",
      rows: [],
    });
  const anchors = new Map<string, string>();
  for (const group of groups)
    for (const row of group.rows) {
      if (row.kind === "tools")
        for (const tool of row.tools)
          if (tool.toolCallId) anchors.set(tool.toolCallId, group.id);
    }
  return (
    <>
      {groups.map((group, index) => (
        <Fragment key={group.presentationKey}>
          {group.id === "ungrouped" &&
          !pending &&
          threadId !== BOT_THREAD_ID ? (
            <Rows
              key={group.presentationKey}
              rows={questionRows(group.rows)}
              threadId={threadId}
            />
          ) : (
            <Turn
              key={group.presentationKey}
              {...group}
              pending={index === groups.length - 1 ? pending : undefined}
              entries={
                group.turn ? indexed.byTurn.get(group.turn.turn_id)! : entries
              }
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
          )}
          {connectorOffers
            .filter(
              (offer) =>
                offer.tool_call_id &&
                anchors.get(offer.tool_call_id) === group.id,
            )
            .map((offer) => (
              <ConnectorOffer key={offer.offer_id} offer={offer} />
            ))}
        </Fragment>
      ))}
      {connectorOffers
        .filter(
          (offer) => !offer.tool_call_id || !anchors.has(offer.tool_call_id),
        )
        .map((offer) => (
          <ConnectorOffer key={offer.offer_id} offer={offer} />
        ))}
    </>
  );
}

function Turn(props: {
  pending?: PendingInteraction;
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
  const currentFailureRunId = useContext(CurrentFailureRun);
  const saved = useMemo(
    () =>
      savedRows(
        loaded,
        savedToolGroups(loaded),
        continuation,
        currentFailureRunId,
        props.threadId,
      ),
    [loaded, continuation, currentFailureRunId, props.threadId],
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
// row always remains authoritative.
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
    // A live quotation can still find the same input-relative slot after the
    // turn is saved and remounted. Saved selections prefer exact entry offsets.
    return row.kind === "assistant" && boundary !== "turn"
      ? { ...result, replySlot: slot, savedReplyAnchor: row.readingAnchor }
      : result;
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
  pending,
}: {
  pending?: PendingInteraction;
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
  const { t } = useTranslation();
  const rows = usePresentedRows(
    questionRows(
      sourceRows.map((row) =>
        row.kind === "input" &&
        row.position === turn?.input_position &&
        turn &&
        !row.parts.some(inputSource)
          ? { ...row, id: `input:${turn.turn_id}` }
          : row,
      ),
      pending,
    ),
    !missing,
  );
  const mountedExecution = useRef(new Set<string>());
  const complete =
    turn?.final_position != null &&
    rows.every((row) => row.position !== undefined || row.publication);
  const stopped = useContext(TerminalTurn);
  const stoppedAt =
    stopped && stopped.id === turn?.turn_id ? stopped.time : undefined;
  const actions = useContext(MessageBranches);
  const lastProse =
    stoppedAt && !missing
      ? rows.findLast(
          (row) =>
            row.kind === "assistant" &&
            !row.subagentRunId &&
            !row.live &&
            row.text.trim(),
        )
      : undefined;
  const outputPosition =
    lastProse?.position ?? turn?.output_position ?? turn?.final_position;
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
        (row.kind === "assistant" &&
          (!complete || row.publication || row.position === outputPosition)) ||
        row.kind === "failure" ||
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
    } else if (row.kind === "assistant" && kind === "visible") textIndex++;
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
          kind === "execution"
            ? ((complete
                ? [...mountedExecution.current].find((key) =>
                    key.startsWith(`${boundary}:execution:`),
                  )
                : undefined) ?? `${boundary}:execution:${textIndex}`)
            : row.id,
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
                t("Loading turn…")
              ) : failed && retry ? (
                <>
                  {t("Turn could not be fully loaded.")}{" "}
                  <button type="button" onClick={retry}>
                    {t("Retry loading turn")}
                  </button>
                </>
              ) : (
                t("Turn history is incomplete.")
              )}
            </div>
          );
        if (segment.kind === "visible")
          return (
            <Rows
              key={segment.id}
              rows={segment.rows}
              threadId={threadId}
              output={stoppedAt ? [] : output}
              turn={turn}
              messageActions
            />
          );
        return (
          <ExecutionSegment
            key={segment.id}
            rows={segment.rows}
            threadId={threadId}
            complete={complete}
            durationMs={
              visibleSegments.filter((item) => item.kind === "execution")
                .length === 1
                ? turn?.duration_ms
                : undefined
            }
          />
        );
      })}
      {turn && stoppedAt && (
        <div className={styles.assistantMessage}>
          {stopped?.notice}
          <CopyMessage
            timestamp={stoppedAt}
            text={output.map((row) => row.text).join("\n\n")}
            truncated={output.some((row) => row.truncated)}
            action={
              actions?.points?.[turn.turn_id]?.branch && (
                <MessageActionButton
                  kind="branch"
                  disabled={actions.pending}
                  onClick={() => void actions.create(turn.turn_id)}
                />
              )
            }
          />
        </div>
      )}
    </section>
  );
}

function ExecutionSegment({
  rows,
  threadId,
  complete,
  durationMs,
}: {
  durationMs?: number | null;
  rows: Row[];
  threadId: string;
  complete: boolean;
}) {
  const { t } = useTranslation();
  const duration = executionTime(durationMs);
  const { preview, running } = complete
    ? {
        preview: duration
          ? t("Worked for {{duration}}", { duration })
          : t("Worked"),
        running: false,
      }
    : executionSummary(rows, t);
  return (
    <ExecutionDetails complete={complete} preview={preview} running={running}>
      {(open) => (
        <ExecutionReader open={open} complete={complete}>
          <Rows rows={rows} threadId={threadId} continuation />
        </ExecutionReader>
      )}
    </ExecutionDetails>
  );
}

function executionSummary(
  rows: Row[],
  t: ReturnType<typeof useTranslation>["t"],
) {
  const counts = new Map<string, Set<string>>();
  let activeStep: string | undefined;
  let thinking = false;
  let notices = 0;
  const otherActivities = new Set<string>();
  for (const row of rows) {
    if (row.kind === "tools") {
      for (const tool of row.tools) {
        const info = describeTool(tool);
        if (info.running)
          activeStep = [toolLabel(info, t), toolSummary(info, t)]
            .filter(Boolean)
            .join(" ");
        const action =
          info.kind === "file"
            ? "Read {{count}} files"
            : info.kind === "edit"
              ? "Edit {{count}} files"
              : tool.name === "shell_exec"
                ? "Run {{count}} commands"
                : info.kind === "search"
                  ? "Run {{count}} searches"
                  : "Call {{count}} tools";
        const id =
          (info.kind === "file" || info.kind === "edit") && info.path
            ? info.path
            : tool.id;
        const members = counts.get(action);
        if (members) members.add(id);
        else counts.set(action, new Set([id]));
      }
    } else if (row.kind === "thinking") {
      thinking = true;
      if (row.segments.some((part) => part.running))
        activeStep = t("Thinking", { context: "active" });
    } else if (row.kind === "notifications") notices += row.notices.length;
    else if (row.kind === "context")
      otherActivities.add(
        row.context === "handoff" ? t("Summary") : t("Compact Summary"),
      );
    else if (
      row.kind === "origin" ||
      (row.kind === "activity" && row.childName)
    )
      otherActivities.add(t("Subagents"));
    else if (row.kind === "activity")
      otherActivities.add(row.name || t("Activity"));
  }
  if (activeStep) return { preview: activeStep, running: true };
  const labels = Array.from(counts, ([action, members]) =>
    t(action, { count: members.size }),
  );
  labels.push(...otherActivities);
  if (notices) labels.push(t("System update count", { count: notices }));
  const preview = labels.length
    ? labels.join(", ")
    : thinking
      ? t("Thought")
      : t("View the available execution steps");
  return { preview, running: false };
}

function ExecutionReader({
  open,
  complete,
  children,
}: {
  open: boolean;
  complete: boolean;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const reader = useRef<HTMLDivElement>(null);
  const follow = useRef(!complete);
  const anchor = useRef<ReadingAnchor | undefined>(undefined);
  const start = useRef<HTMLDivElement>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const root = reader.current;
    if (
      !open ||
      !root ||
      !start.current ||
      !end.current ||
      typeof IntersectionObserver === "undefined"
    )
      return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          root.toggleAttribute(
            entry.target === start.current
              ? "data-more-above"
              : "data-more-below",
            !entry.isIntersecting,
          );
        }
      },
      { root },
    );
    observer.observe(start.current);
    observer.observe(end.current);
    return () => observer.disconnect();
  }, [open]);
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
      role="region"
      aria-label={t("Work log")}
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
      <div ref={start} aria-hidden="true" />
      {children}
      <div ref={end} aria-hidden="true" />
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
        <RecoveryText recovery={recovery} />
      </span>
    </div>
  );
}

function GapNotice() {
  const { t } = useTranslation();
  return (
    <p role="status">
      {t(
        "Some live updates are missing. Check saved history and run details for the full result.",
      )}
    </p>
  );
}

export function LiveOutput({
  blocks,
  gap,
  running = false,
  threadId,
  label,
}: {
  label?: string;
  blocks: DisplayBlock[];
  gap: boolean;
  running?: boolean;
  threadId?: string;
}) {
  const { t } = useTranslation();
  const rows = liveRows(blocks, running, threadId);
  return (
    <section
      className={styles.liveOutput}
      aria-label={t("Current unsaved output")}
    >
      {rows.length > 0 && label && <small>{label}</small>}
      <Rows rows={rows} threadId={threadId} />
      {gap && <GapNotice />}
    </section>
  );
}
