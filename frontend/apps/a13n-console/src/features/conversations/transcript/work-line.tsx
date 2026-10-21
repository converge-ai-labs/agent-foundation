import { CaretRightIcon } from "@phosphor-icons/react";
import type { TFunction } from "i18next";
import { Fragment, useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { formatDuration } from "../format";
import { DiffBadges } from "./diff-badges";
import { ItemContents } from "../stored-content";
import {
  entryGlyph,
  entryLabel,
  entrySubject,
  entryWait,
  StateMark,
  waitingLabel,
  type WorkState,
} from "./entry-language";
import { workSummary, type WorkEntry, type WorkSummary } from "./items";
import styles from "./work-line.module.css";

/**
 * One quiet line stands for everything the agent did between two replies: it
 * says what happened in words, and opens into the steps themselves. Every name
 * and glyph comes from the same owner the Debug timeline reads.
 */
export function WorkLine({
  entries,
  childPath,
}: {
  entries: readonly WorkEntry[];
  /** Where the child thread this work delegated to can be opened, if known. */
  childPath?: string | null;
}) {
  const { t } = useTranslation();
  const summary = workSummary(entries);
  const [open, setOpen] = useState(false);
  if (!entries.length) return null;
  const delegated = entries.some((work) => work.entry.kind === "subagent");
  return (
    <div className={styles.work}>
      <div className={styles.workLine}>
        <button
          type="button"
          className={styles.workTrigger}
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          <StateMark state={lineState(summary)} size={13} decorative />
          <span className={styles.workText} title={workTitle(summary, t)}>
            {/* The card below names the call; the line only says whose turn it is. */}
            {summary.waiting ? (
              waitingLabel(entryWait(summary.waiting), t)
            ) : (
              <>
                {summary.named.map((work, index) => (
                  <Fragment key={work.id}>
                    {index > 0 && ", "}
                    <Phrase entry={work.entry} />
                  </Fragment>
                ))}
                {!!summary.more && (
                  <>
                    {summary.named.length ? " " : ""}
                    {t("and {{count}} more", { count: summary.more })}
                  </>
                )}
              </>
            )}
          </span>
          {summary.durationMs !== null && !summary.waiting && (
            <span className={styles.workDuration}>
              {formatDuration(summary.durationMs)}
            </span>
          )}
          <CaretRightIcon
            size={12}
            aria-hidden="true"
            className={styles.workCaret}
            data-expanded={open || undefined}
          />
        </button>
        {delegated && childPath && (
          <Link className={styles.workLink} to={childPath}>
            {t("Open child thread")}
          </Link>
        )}
      </div>
      {open && (
        <ol className={styles.workRows}>
          {entries.map((work) => (
            <WorkRow key={work.id} work={work} />
          ))}
        </ol>
      )}
    </div>
  );
}

/** The whole line as text, so a truncated line still reads in full on hover. */
function workTitle(summary: WorkSummary, t: TFunction) {
  if (summary.waiting) return undefined;
  const named = summary.named
    .map(({ entry }) =>
      [entryLabel(entry, t).name, entrySubject(entry)]
        .filter(Boolean)
        .join(" "),
    )
    .join(", ");
  return summary.more
    ? [named, t("and {{count}} more", { count: summary.more })]
        .filter(Boolean)
        .join(" ")
    : named;
}

function lineState(summary: WorkSummary): WorkState {
  if (summary.waiting) return "waiting";
  if (summary.running) return "working";
  if (summary.failed) return "failed";
  if (summary.interrupted) return "interrupted";
  return summary.handedOff ? "handed_off" : "done";
}

/** The step's name, monospace only when it is a tool's own wire name. */
function Name({ entry }: { entry: WorkEntry["entry"] }) {
  const { t } = useTranslation();
  const label = entryLabel(entry, t);
  return (
    <span className={styles.workName} data-mono={label.mono || undefined}>
      {label.name}
    </span>
  );
}

/** The same name the row carries, followed by what the step was about. */
function Phrase({ entry }: { entry: WorkEntry["entry"] }) {
  const subject = entrySubject(entry);
  return (
    <>
      <Name entry={entry} />
      {subject && <span className={styles.workSubject}> {subject}</span>}
    </>
  );
}

/**
 * Every row fills the same columns, so edits, durations and outcomes line up
 * however much a step had to say. Reasoning keeps its place in the count and
 * shows the same excerpt the Debug timeline shows.
 */
function WorkRow({ work }: { work: WorkEntry }) {
  const entry = work.entry;
  const Glyph = entryGlyph(entry);
  const subject = entrySubject(entry);
  // Reasoning is content, not an action: it has no outcome to time.
  const content = entry.kind === "reasoning";
  const edit = "edit" in entry ? entry.edit : null;
  return (
    <li className={styles.workRow} data-message-id={work.id}>
      <span className={styles.workGlyph} aria-hidden="true">
        <Glyph size={13} />
      </span>
      <Name entry={entry} />
      <span className={styles.workSubject} title={subject || undefined}>
        {subject}
      </span>
      <span className={styles.workRowDiff}>
        {edit && <DiffBadges diff={edit.diff} />}
      </span>
      <span className={styles.workRowDuration}>
        {content || entry.durationMs === null
          ? ""
          : formatDuration(entry.durationMs)}
      </span>
      {content ? <span /> : <StateMark state={work.state} size={13} />}
      <ItemContents
        itemId={entry.itemId ?? entry.id}
        className={styles.workRowContent}
      />
    </li>
  );
}
