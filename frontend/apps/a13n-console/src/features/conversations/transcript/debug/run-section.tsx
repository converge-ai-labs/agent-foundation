import { Button } from "a13n-ui";
import { CaretRightIcon } from "@phosphor-icons/react";
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../../../layout/workspace";
import type { Schema } from "../../../../shared/api";
import { StatePill } from "../../../../shared/feedback";
import { JsonView } from "../../../../shared/forms";
import { CopyButton } from "../../../../shared/identity";
import { MarkdownContent } from "../../../../shared/markdown";
import { isActiveRun } from "../../api";
import { formatCost } from "../../../../shared/cost";
import { formatDuration, resultExcerpt, tokenFigures } from "../../format";
import { runOutcome } from "../../lifecycle";
import { runRequest, type RunRequest } from "../../request";
import { ForkRun } from "../../fork-run";
import type { RunTimeline } from "../../timeline";
import { MessageAuthor } from "../../message-author";
import { RequestContent, requestLabel } from "../user-message";
import { isInteractive } from "../run-actions";
import {
  childThreadOf,
  childThreadPath,
  useChildThreads,
} from "../thread-runs";
import { useRunOpen } from "./collapse";
import { RunDetailsDialog } from "./run-details";
import { EventRow, TimelineRows } from "./timeline-rows";
import { useNow } from "./use-now";
import type { RunScope } from "./scope";
import styles from "./debug.module.css";

/**
 * One Run, read as the executor's record of it: what it was given, the steps
 * it took and what it returned, under a header that says how it went.
 */
export function DebugRunSection({
  run,
  thread,
  timeline,
  index,
  resubmit,
  jumpToDock = () => {},
}: {
  run: Schema["RunView"];
  thread: Schema["ThreadView"];
  /** The same reading of the Run the Chat level renders. */
  timeline: RunTimeline;
  /** Position in the Thread; omitted while the Thread's Runs are unknown. */
  index: number | null;
  /** Prefills the dock with this stopped Run's message, when it may be sent again. */
  resubmit?: () => void;
  jumpToDock?: () => void;
}) {
  const { t } = useTranslation();
  const { can, basePath } = useWorkspace();
  const [open, toggleOpen] = useRunOpen(run.id);
  const active = isActiveRun(run.status) || run.status === "waiting";
  const now = useNow(active);
  const children = useChildThreads(run.session_id, run.thread_id);
  const child = childThreadOf(children, run.id);
  const started = Date.parse(run.started_at ?? run.created_at);
  const ended = run.sealed_at ? Date.parse(run.sealed_at) : now;
  const duration =
    timeline.totals.durationMs ?? (active ? ended - started : null);
  const scope: RunScope = {
    run,
    start: started,
    span: Math.max(ended - started, 1),
    child: child && {
      path: childThreadPath(basePath, child),
      runs: child.runs.length,
    },
    answerable: isInteractive(thread) && can("run"),
    jumpToDock,
  };
  const outcome = runOutcome(run);
  const request = runRequest(run, thread);
  return (
    <section className={styles.section} data-run={run.id}>
      <header className={styles.heading}>
        <div className={styles.headingLine}>
          <button
            type="button"
            className={styles.headingTitle}
            aria-expanded={open}
            onClick={toggleOpen}
          >
            <CaretRightIcon
              size={12}
              className={styles.caret}
              data-expanded={open || undefined}
              aria-hidden="true"
            />
            <span className={styles.runName}>
              {index === null ? t("Run") : t("Run {{index}}", { index })}
            </span>
          </button>
          <StatePill state={run.status} />
          <Lineage run={run} />
          <span className={styles.headingActions}>
            <ForkRun
              run={run}
              thread={thread}
              level="debug"
              index={index}
              compact
            />
            <RunDetailsDialog run={run} index={index} />
          </span>
        </div>
        <p className={styles.headingMeta}>
          <StartTime at={run.started_at ?? run.created_at} />
          <span>{formatDuration(duration)}</span>
          <span>
            {t("{{count}} model calls", { count: timeline.totals.modelCalls })}
          </span>
          <TokenFigures totals={timeline.totals} />
          {timeline.totals.costUsd && (
            <span>{formatCost(timeline.totals.costUsd)}</span>
          )}
        </p>
      </header>
      <div className={styles.body}>
        <Field
          label={t("Input")}
          source={
            request.kind === "message" ? (
              <MessageAuthor
                entryId={run.source_entry_id}
                principalId={run.principal_id}
                plain
              />
            ) : (
              requestLabel(request, t)
            )
          }
        >
          {open ? (
            <Clamp>
              <RequestContent request={request} />
            </Clamp>
          ) : (
            // A closed run keeps one line of what it was given.
            <p className={`${styles.well} ${styles.excerpt}`}>
              {resultExcerpt(request.text, 240) || t("No request text")}
            </p>
          )}
        </Field>
        {open && (
          <>
            <Field label={t("Steps")}>
              {timeline.coverage !== "complete" && (
                <p role="status" className={styles.coverage}>
                  {t(
                    timeline.coverage === "unavailable"
                      ? "Execution history is unavailable; showing retained messages."
                      : "Execution history is incomplete; showing retained messages.",
                  )}
                </p>
              )}
              <TimelineRows entries={timeline.entries} scope={scope} />
              {outcome && (
                <EventRow
                  entry={outcome}
                  scope={scope}
                  action={
                    resubmit && (
                      <Button size="xs" variant="outline" onClick={resubmit}>
                        {t("Resubmit")}
                      </Button>
                    )
                  }
                />
              )}
            </Field>
            <RunOutput output={run.output} />
          </>
        )}
      </div>
    </section>
  );
}

/** A Run submitted from this view, before the Service has accepted it. */
export function DebugPendingRun({ request }: { request: RunRequest }) {
  const { t } = useTranslation();
  return (
    <section className={styles.section} aria-busy="true">
      <header className={styles.heading}>
        <div className={styles.headingLine}>
          <span className={styles.pendingName}>{t("Run")}</span>
          <StatePill state="accepted" />
        </div>
      </header>
      <div className={styles.body}>
        <Field label={t("Input")}>
          <Clamp>
            <RequestContent request={request} />
          </Clamp>
        </Field>
      </div>
    </section>
  );
}

/** One labelled part of a Run's record: its input, its steps or its output. */
function Field({
  label,
  source,
  action,
  children,
}: {
  label: string;
  /** Where the part came from, read as a fact beside its label. */
  source?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={styles.field}>
      <div className={styles.fieldLabel}>
        <span>{label}</span>
        {source && <span className={styles.fieldSource}>{source}</span>}
        {action && <span className={styles.fieldAction}>{action}</span>}
      </div>
      {children}
    </section>
  );
}

/** What the Run returned: its text, or the structure its output spec asked for. */
function RunOutput({ output }: { output: Schema["RunView"]["output"] }) {
  const { t } = useTranslation();
  if (output == null || output === "") return null;
  if (typeof output !== "string")
    return (
      <Field label={t("Output")}>
        <JsonView value={output} />
      </Field>
    );
  return (
    <Field
      label={t("Output")}
      action={
        <CopyButton value={output} iconOnly copyLabel={t("Copy output")} />
      }
    >
      <Clamp tall>
        <MarkdownContent text={output} />
      </Clamp>
    </Field>
  );
}

/**
 * A long input or output stays bounded so the next Run is never far away; the
 * reader opens the rest in place.
 */
function Clamp({
  tall = false,
  children,
}: {
  tall?: boolean;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const frame = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const [overflows, setOverflows] = useState(false);
  const [expanded, setExpanded] = useState(false);
  useLayoutEffect(() => {
    const outer = frame.current;
    const inner = content.current;
    if (!outer || !inner || expanded) return;
    const measure = () =>
      setOverflows(inner.offsetHeight > outer.clientHeight + 1);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(inner);
    return () => observer.disconnect();
  }, [expanded]);
  return (
    <div className={styles.well}>
      <div
        ref={frame}
        className={styles.clamp}
        data-tall={tall || undefined}
        data-expanded={expanded || undefined}
        data-overflows={overflows || undefined}
      >
        <div ref={content}>{children}</div>
      </div>
      {(overflows || expanded) && (
        <button
          type="button"
          className={styles.clampToggle}
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          {t(expanded ? "Show less" : "Show all")}
        </button>
      )}
    </div>
  );
}

/** When the Run started: the time today, the date as well on another day. */
function StartTime({ at }: { at: string }) {
  const { i18n } = useTranslation();
  const date = new Date(at);
  const today = date.toDateString() === new Date().toDateString();
  const format = new Intl.DateTimeFormat(i18n?.resolvedLanguage, {
    ...(!today && { dateStyle: "medium" }),
    timeStyle: "short",
  });
  return (
    <time dateTime={at} title={date.toLocaleString(i18n?.resolvedLanguage)}>
      {format.format(date)}
    </time>
  );
}

/** Tokens nobody reported are unknown, never zero, so they are not shown. */
function TokenFigures({ totals }: { totals: RunTimeline["totals"] }) {
  const { t } = useTranslation();
  if (!totals.reportedUsage) return null;
  const figures = tokenFigures(t, {
    input: totals.inputTokens,
    output: totals.outputTokens,
    cached: totals.cacheReadTokens,
  });
  return (
    <>
      <span title={figures.pairTitle}>{figures.pair}</span>
      {figures.cache && <span title={figures.cacheTitle}>{figures.cache}</span>}
    </>
  );
}

/** Where this Run came from, when it was not simply the next one. */
function Lineage({ run }: { run: Schema["RunView"] }) {
  const { t } = useTranslation();
  const notes = [
    run.lineage === "fork" && t("fork"),
    run.attempts > 1 && t("{{count}} attempts", { count: run.attempts }),
  ].filter(Boolean) as string[];
  if (!notes.length) return null;
  return <span className={styles.lineage}>{notes.join(" · ")}</span>;
}
