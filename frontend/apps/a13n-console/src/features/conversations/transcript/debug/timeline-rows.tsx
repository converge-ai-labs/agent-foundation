import type { TFunction } from "i18next";
import { Badge } from "a13n-ui";
import { CheckIcon, SlidersHorizontalIcon } from "@phosphor-icons/react";
import { useState, type CSSProperties, type ReactNode } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useAgent } from "../../../agents/queries";
import { ModelKeyLabel } from "../../../models/model-icon";
import { formatDuration, formatOffset, tokenFigures } from "../../format";
import { useRunAgent } from "../../queries";
import { overriddenFields } from "../../run-agent";
import type {
  ActionEntry,
  EventEntry,
  ModelEntry,
  TimelineEntry,
} from "../../timeline";
import { DiffBadges } from "../diff-badges";
import {
  asksAQuestion,
  entryGlyph,
  entryLabel,
  entrySubject,
  StateMark,
  workState,
  type WorkState,
} from "../entry-language";
import { EntryPane } from "./entry-pane";
import type { RunScope } from "./scope";
import styles from "./debug.module.css";

/**
 * The Run's steps as lines of its log. Model requests are the skeleton; what
 * they emitted indents beneath them, and each line opens its record in place.
 */
export function TimelineRows({
  entries,
  scope,
  depth = 0,
}: {
  entries: readonly TimelineEntry[];
  scope: RunScope;
  depth?: number;
}) {
  return entries.map((entry) =>
    entry.kind === "event" ? (
      <EventRow key={entry.id} entry={entry} scope={scope} depth={depth} />
    ) : (
      <StepRow key={entry.id} entry={entry} scope={scope} depth={depth} />
    ),
  );
}

function StepRow({
  entry,
  scope,
  depth,
}: {
  entry: TimelineEntry;
  scope: RunScope;
  depth: number;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const Glyph = entryGlyph(entry);
  // Reasoning and replies are said, not done, and a group of other
  // observations is not one step: neither carries an outcome or a duration.
  const content = "text" in entry || entry.kind === "other";
  const children = "children" in entry ? entry.children : [];
  const state = content
    ? "text" in entry && entry.state === "interrupted"
      ? "interrupted"
      : "done"
    : workState(entry.state, scope.run.status);
  const childPath =
    entry.kind === "subagent" ? (scope.child?.path ?? null) : null;
  const text = rowText(entry, state, t, childPath);
  const extra = (childPath || ("edit" in entry && entry.edit)) && (
    <span className={styles.extra}>
      {childPath && (
        <Link className={styles.lineLink} to={childPath}>
          {t("Open child thread")}
        </Link>
      )}
      {"edit" in entry && entry.edit && <DiffBadges diff={entry.edit.diff} />}
    </span>
  );
  return (
    <>
      <LogLine
        kind={entry.kind}
        depth={depth}
        open={open}
        // A group of other observations has no one moment to place it at.
        time={
          entry.kind === "other"
            ? ""
            : formatOffset(offsetOf(entry.startedAt, scope))
        }
        mark={
          // A step that completed needs no mark; an exception takes the
          // glyph's place at the head of its line.
          state === "done" || state === "unknown" ? (
            <Glyph size={13} aria-hidden="true" />
          ) : (
            <StateMark state={state} size={13} />
          )
        }
        name={text.name}
        mono={text.mono}
        subject={text.subject}
        extra={extra}
        cache={text.cache && <span title={text.cacheTitle}>{text.cache}</span>}
        tokens={
          text.tokens && <span title={text.tokensTitle}>{text.tokens}</span>
        }
        duration={
          content || entry.durationMs === null
            ? null
            : formatDuration(entry.durationMs)
        }
        onToggle={() => setOpen((value) => !value)}
      />
      {open && (
        <div
          className={styles.detail}
          style={{ "--depth": depth } as CSSProperties}
        >
          <EntryPane entry={entry} scope={scope} />
        </div>
      )}
      {!!children.length && (
        <TimelineRows entries={children} scope={scope} depth={depth + 1} />
      )}
    </>
  );
}

/**
 * One line of the log: when it happened, what it was, and the figures in
 * fixed columns. The line's name opens it; links in it stay ordinary links.
 */
function LogLine({
  kind,
  depth,
  open,
  tone,
  time,
  mark,
  name,
  mono = false,
  subject,
  extra,
  cache,
  tokens,
  duration,
  popup = false,
  onToggle,
}: {
  kind: string;
  depth: number;
  open?: boolean;
  tone?: string;
  time: string;
  mark: ReactNode;
  name: string;
  mono?: boolean;
  subject?: ReactNode;
  extra?: ReactNode;
  cache?: ReactNode;
  tokens?: ReactNode;
  duration?: ReactNode;
  /** The line opens a dialog rather than its record in place. */
  popup?: boolean;
  onToggle?: () => void;
}) {
  const label = (
    <>
      <span className={styles.mark}>{mark}</span>
      <span className={styles.name} data-mono={mono || undefined}>
        {name}
      </span>
      {subject && (
        <span
          className={styles.subject}
          title={typeof subject === "string" ? subject : undefined}
        >
          {subject}
        </span>
      )}
    </>
  );
  return (
    <div
      className={styles.line}
      data-kind={kind}
      data-tone={tone}
      data-depth={depth}
      data-open={open || undefined}
    >
      <span className={styles.time}>{time}</span>
      <span
        className={styles.lineBody}
        style={{ "--depth": depth } as CSSProperties}
      >
        {onToggle ? (
          <button
            type="button"
            className={styles.lineTrigger}
            aria-expanded={popup ? undefined : !!open}
            aria-haspopup={popup ? "dialog" : undefined}
            onClick={onToggle}
          >
            {label}
          </button>
        ) : (
          <span className={styles.lineLabel}>{label}</span>
        )}
        {extra}
      </span>
      <span className={styles.cache}>{cache}</span>
      <span className={styles.tokens}>{tokens}</span>
      <span className={styles.duration}>{duration}</span>
    </div>
  );
}

/** Milliseconds from the Run's start, when the moment is known. */
function offsetOf(at: string | null, scope: RunScope) {
  if (!at) return null;
  const offset = Date.parse(at) - scope.start;
  return Number.isFinite(offset) ? offset : null;
}

interface RowText {
  name: string;
  mono: boolean;
  subject?: string;
  tokens?: string;
  tokensTitle?: string;
  /** How much of the request's input the provider served from its cache. */
  cache?: string;
  cacheTitle?: string;
}

function rowText(
  entry: TimelineEntry,
  state: WorkState,
  t: TFunction,
  childPath: string | null,
): RowText {
  if (entry.kind === "model") return modelText(entry, t);
  if ("arguments" in entry) return actionText(entry, state, t, childPath);
  return { ...entryLabel(entry, t), subject: entrySubject(entry) };
}

function modelText(entry: ModelEntry, t: TFunction): RowText {
  const actions = entry.children.filter(
    (child): child is ActionEntry => "arguments" in child,
  );
  const asked = actions.find((child) => child.hitl);
  const outcome = entry.errorCode
    ? t("failed · {{reason}}", { reason: failureReason(entry, t) })
    : entry.state === "failed"
      ? t("state.failed")
      : asked
        ? t(asksAQuestion(asked) ? "asked a question" : "asked for approval")
        : actions.length
          ? t("called {{count}} tools", { count: actions.length })
          : entry.children.some((child) => child.kind === "reply")
            ? t("wrote the reply")
            : entry.state === "interrupted"
              ? t("interrupted")
              : entry.endedAt
                ? t("no output")
                : t("thinking…");
  const label = {
    ...entryLabel(entry, t),
    subject: [entry.model, outcome].filter(Boolean).join(" · "),
  };
  const usage = entry.usage;
  // Tokens nobody reported are unknown: the pair is absent, not zeroed.
  if (!usage || !entry.reportedUsage) return label;
  const figures = tokenFigures(t, {
    input: usage.inputTokens,
    output: usage.outputTokens,
    cached: usage.cacheReadTokens,
  });
  return {
    ...label,
    tokens: figures.pair,
    tokensTitle: figures.pairTitle,
    cache: figures.rate,
    cacheTitle: figures.cacheTitle,
  };
}

/** The provider's status and message name a failure better than the generic code they replace. */
function failureReason(entry: ModelEntry, t: TFunction) {
  return [
    entry.errorStatus !== null &&
      t("HTTP {{status}}", { status: entry.errorStatus }),
    entry.errorDetail ?? entry.errorCode,
  ]
    .filter(Boolean)
    .join(" · ");
}

function actionText(
  entry: ActionEntry,
  state: WorkState,
  t: TFunction,
  childPath: string | null,
): RowText {
  const notes = [
    entrySubject(entry),
    // Without a link to follow, the row still says where the work went.
    entry.dispatchOnly && !childPath && t("child thread"),
    state === "waiting" &&
      t(
        asksAQuestion(entry)
          ? "waiting for your answer"
          : "waiting for your approval",
      ),
  ].filter(Boolean) as string[];
  return { ...entryLabel(entry, t), subject: notes.join(" · ") };
}

/** A lifecycle fact: its head carries its tone, what follows stays secondary. */
export function EventRow({
  entry,
  scope,
  depth = 0,
}: {
  entry: EventEntry;
  scope: RunScope;
  depth?: number;
}) {
  const { t } = useTranslation();
  const Glyph = entryGlyph(entry);
  return (
    <LogLine
      kind="event"
      tone={entry.notice.tone}
      depth={depth}
      time={formatOffset(offsetOf(entry.occurredAt, scope))}
      mark={<Glyph size={13} aria-hidden="true" />}
      name={entryLabel(entry, t).name}
      subject={entrySubject(entry)}
    />
  );
}

/**
 * The first line of the log: the agent the Run executed, at which version and
 * on which model. It opens the Run's whole agent configuration.
 */
export function AgentRow({
  scope,
  onOpen,
}: {
  scope: RunScope;
  onOpen: () => void;
}) {
  const { t } = useTranslation();
  const { run } = scope;
  const agent = useAgent(run.agent_id);
  const executed = useRunAgent(run.id);
  const overrides = overriddenFields(run).length;
  return (
    <LogLine
      kind="agent"
      depth={0}
      time=""
      mark={<SlidersHorizontalIcon size={13} aria-hidden="true" />}
      name={t("Agent")}
      subject={
        <>
          <span className={styles.lineAgent}>{agent.data?.name}</span>
          {executed.data && (
            <>
              <span className={styles.lineVersion}>
                v{executed.data.revision.number}
              </span>
              <ModelKeyLabel
                className={styles.lineModel}
                modelKey={executed.data.config.model}
                size={13}
              />
            </>
          )}
          {!!overrides && (
            <Badge variant="info" size="sm">
              {t("{{count}} overrides", { count: overrides })}
            </Badge>
          )}
        </>
      }
      popup
      onToggle={onOpen}
    />
  );
}

/** The last line of a Run that finished: when it did. */
export function CompletedRow({ scope }: { scope: RunScope }) {
  const { t } = useTranslation();
  return (
    <LogLine
      kind="event"
      tone="success"
      depth={0}
      time={formatOffset(offsetOf(scope.run.sealed_at, scope))}
      mark={<CheckIcon size={13} aria-hidden="true" />}
      name={t("Run completed")}
    />
  );
}
