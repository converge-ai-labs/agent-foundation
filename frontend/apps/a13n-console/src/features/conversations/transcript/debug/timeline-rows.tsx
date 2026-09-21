import type { TFunction } from "i18next";
import { useState, type CSSProperties, type ReactNode } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { CopyButton } from "../../../../shared/identity";
import { MarkdownContent } from "../../../../shared/markdown";
import { formatDuration, formatTokens } from "../../format";
import { lifecycleNotice, type LifecycleNotice } from "../../lifecycle";
import type {
  ActionEntry,
  ContentEntry,
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
import { bar, type RunScope } from "./scope";
import styles from "./debug.module.css";

/** Model requests are the skeleton; what they emitted indents beneath them. */
export function TimelineRows({
  entries,
  scope,
  nested = false,
}: {
  entries: readonly TimelineEntry[];
  scope: RunScope;
  nested?: boolean;
}) {
  if (!entries.length) return null;
  return (
    <div className={nested ? styles.children : styles.timeline}>
      {entries.map((entry) => (
        <TimelineRow key={entry.id} entry={entry} scope={scope} />
      ))}
    </div>
  );
}

function TimelineRow({
  entry,
  scope,
}: {
  entry: TimelineEntry;
  scope: RunScope;
}) {
  if (entry.kind === "reply") return <ReplyBlock entry={entry} />;
  if (entry.kind === "event") return <EventRow entry={entry} scope={scope} />;
  return <ExpandableRow entry={entry} scope={scope} />;
}

function ExpandableRow({
  entry,
  scope,
}: {
  entry: TimelineEntry;
  scope: RunScope;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const Glyph = entryGlyph(entry);
  // Reasoning is something the agent said, not something it did: it carries no
  // outcome, no duration and no place on the time bar.
  const content = entry.kind === "reasoning";
  const children = "children" in entry ? entry.children : [];
  // A reply is the answer, not a step: it leaves the indent and reads as prose.
  const nested = children.filter((child) => child.kind !== "reply");
  const replies = children.filter(
    (child): child is ContentEntry => child.kind === "reply",
  );
  const state = workState(entry.state, scope.run.status);
  const childPath =
    entry.kind === "subagent" ? (scope.child?.path ?? null) : null;
  const text = rowText(entry, state, t, childPath);
  const track = bar(scope, entry.startedAt, entry.durationMs);
  return (
    <div className={styles.entry} data-kind={entry.kind}>
      <div className={styles.row} data-open={open || undefined}>
        <button
          type="button"
          className={styles.rowTrigger}
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          <span className={styles.marker} aria-hidden="true">
            <Glyph size={13} />
          </span>
          <span className={styles.rowBody}>
            <span className={styles.name} data-mono={text.mono || undefined}>
              {text.name}
            </span>
            {text.subject && (
              <span className={styles.subject} title={text.subject}>
                {text.subject}
              </span>
            )}
          </span>
        </button>
        <span className={styles.rowMeta}>
          {childPath && (
            <Link className={styles.rowLink} to={childPath}>
              {t("Open child thread")}
            </Link>
          )}
          {"edit" in entry && entry.edit && (
            <DiffBadges diff={entry.edit.diff} />
          )}
          {text.tokens && <span className={styles.tokens}>{text.tokens}</span>}
          {!content && (
            <>
              <span className={styles.duration}>
                {entry.durationMs === null
                  ? ""
                  : formatDuration(entry.durationMs)}
              </span>
              <span
                className={styles.bar}
                aria-hidden="true"
                style={
                  track
                    ? ({
                        "--bar-left": `${track.left}%`,
                        "--bar-width": `${track.width}%`,
                      } as CSSProperties)
                    : undefined
                }
                data-empty={track ? undefined : true}
              />
              <StateMark state={state} />
            </>
          )}
        </span>
      </div>
      {open && <EntryPane entry={entry} scope={scope} />}
      {!!nested.length && (
        <TimelineRows entries={nested} scope={scope} nested />
      )}
      {replies.map((reply) => (
        <ReplyBlock key={reply.id} entry={reply} />
      ))}
    </div>
  );
}

interface RowText {
  name: string;
  mono: boolean;
  subject?: string;
  tokens?: string;
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
    ? t("failed · {{code}}", { code: entry.errorCode })
    : entry.state === "failed"
      ? t("state.failed")
      : asked
        ? t(asksAQuestion(asked) ? "asked a question" : "asked for approval")
        : actions.length
          ? t("called {{count}} tools", { count: actions.length })
          : entry.children.some((child) => child.kind === "reply")
            ? t("wrote the reply")
            : entry.endedAt
              ? t("no output")
              : t("thinking…");
  const usage = entry.usage;
  return {
    ...entryLabel(entry, t),
    subject: [entry.model, outcome].filter(Boolean).join(" · "),
    // Tokens nobody reported are unknown: the pair is absent, not zeroed.
    tokens:
      usage && entry.reportedUsage
        ? `${formatTokens(usage.inputTokens)} → ${formatTokens(usage.outputTokens)}`
        : undefined,
  };
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

/** The Agent's answer is prose, not a row: it reads at full width. */
function ReplyBlock({ entry }: { entry: ContentEntry }) {
  const { t } = useTranslation();
  return (
    <div
      className={styles.reply}
      data-interrupted={entry.state === "interrupted" || undefined}
    >
      <div className={styles.replyLabel}>
        <span>{t("Reply")}</span>
        {entry.state === "interrupted" && <span>{t("interrupted")}</span>}
        <CopyButton value={entry.text} iconOnly copyLabel={t("Copy reply")} />
      </div>
      <div className={styles.replyBody}>
        <MarkdownContent text={entry.text} />
      </div>
    </div>
  );
}

/** Lifecycle facts stay legible without competing with the work they bracket. */
export function EventRow({
  entry,
  scope,
  action,
}: {
  entry: EventEntry;
  scope: RunScope;
  action?: ReactNode;
}) {
  const { t } = useTranslation();
  const notice = lifecycleNotice(entry);
  if (!notice) return null;
  const Glyph = entryGlyph(entry);
  const offset = Math.max(0, Date.parse(entry.occurredAt) - scope.start);
  return (
    <div className={styles.entry} data-kind="event">
      <div className={styles.eventRow} data-tone={notice.tone}>
        <span className={styles.marker} aria-hidden="true">
          <Glyph size={13} />
        </span>
        <span className={styles.rowBody}>
          <span className={styles.eventName}>{noticeText(notice, t)}</span>
        </span>
        <span className={styles.rowMeta}>
          {action}
          <span className={styles.duration}>
            {Number.isFinite(offset) && offset > 0
              ? `+${formatDuration(offset)}`
              : ""}
          </span>
        </span>
      </div>
    </div>
  );
}

/** The words a lifecycle fact is worth; the decision itself is `lifecycle.ts`. */
function noticeText(notice: LifecycleNotice, t: TFunction): string {
  if (notice.kind === "attempt")
    return notice.reason
      ? t("Attempt {{attempt}} started · {{reason}}", {
          attempt: notice.attempt,
          reason: notice.reason,
        })
      : t("Attempt {{attempt}} started", { attempt: notice.attempt });
  if (notice.kind === "recovery")
    return notice.reason
      ? t("Recovery · {{reason}}: events before this point may be missing", {
          reason: notice.reason,
        })
      : t("Recovery: events before this point may be missing");
  if (notice.kind === "retry")
    return notice.attempt !== null &&
      notice.maxAttempts !== null &&
      notice.delaySeconds !== null
      ? t("Retry {{attempt}} of {{max}} in {{delay}}s", {
          attempt: notice.attempt,
          max: notice.maxAttempts,
          delay: notice.delaySeconds,
        })
      : t("Model retry scheduled");
  const head =
    notice.status === "failed"
      ? t("Run failed")
      : notice.status === "cancelled"
        ? t("Run cancelled")
        : notice.waitingOn === "application"
          ? t("Waiting for the application")
          : t("Waiting for you");
  return [head, notice.reason, notice.message].filter(Boolean).join(" · ");
}
