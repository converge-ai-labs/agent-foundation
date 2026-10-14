import { Button } from "a13n-ui";
import {
  ArrowCounterClockwiseIcon,
  CaretRightIcon,
} from "@phosphor-icons/react";
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../../../layout/workspace";
import type { Schema } from "../../../../shared/api";
import { StatePill } from "../../../../shared/feedback";
import { JsonView } from "../../../../shared/forms";
import { CopyableId, CopyButton } from "../../../../shared/identity";
import { MarkdownContent } from "../../../../shared/markdown";
import { isActiveRun } from "../../api";
import { formatCost } from "../../../../shared/cost";
import {
  formatDuration,
  formatOffset,
  resultExcerpt,
  tokenFigures,
} from "../../format";
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
import { CompletedRow, EventRow, TimelineRows } from "./timeline-rows";
import { useNow } from "./use-now";
import type { RunScope } from "./scope";
import styles from "./debug.module.css";

/**
 * One Run, read as the executor's log of it: a header with its figures, then
 * one timed line for each thing it was given, did and returned.
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
          {/* A closed run keeps one line of what it was given. */}
          {!open && (
            <>
              <span className={styles.headingExcerpt}>
                {resultExcerpt(request.text, 200) || t("No request text")}
              </span>
              <span className={styles.headingDuration}>
                {formatDuration(duration)}
              </span>
            </>
          )}
          <span className={styles.headingActions}>
            {/* Sending a stopped run again is the run's action, beside its
                other two. */}
            {resubmit && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                aria-label={t("Resubmit")}
                onClick={resubmit}
              >
                <ArrowCounterClockwiseIcon size={14} aria-hidden="true" />
                <span data-action-label>{t("Resubmit")}</span>
              </Button>
            )}
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
        {open && (
          <RunFacts run={run} totals={timeline.totals} duration={duration} />
        )}
      </header>
      {open && (
        <RunLog>
          <LogBlock
            time={formatOffset(0)}
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
            <Clamp>
              <RequestContent request={request} />
            </Clamp>
          </LogBlock>
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
          {outcome ? (
            <EventRow entry={outcome} scope={scope} />
          ) : (
            run.status === "completed" && <CompletedRow scope={scope} />
          )}
          <RunOutput output={run.output} />
        </RunLog>
      )}
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
      <RunLog>
        <LogBlock time="" label={t("Input")}>
          <Clamp>
            <RequestContent request={request} />
          </Clamp>
        </LogBlock>
      </RunLog>
    </section>
  );
}

/** The log's frame: a time gutter, then the lines under one column head. */
function RunLog({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <div className={styles.log}>
      <div className={styles.logHead} aria-hidden="true">
        <span className={styles.time}>{t("Time")}</span>
        <span className={styles.logHeadSteps}>{t("Steps")}</span>
        <span className={styles.cache}>{t("Cache")}</span>
        <span className={styles.tokens}>{t("Tokens")}</span>
        <span className={styles.duration}>{t("Duration")}</span>
      </div>
      {children}
    </div>
  );
}

/** The Run's figures, each named, on one line under its name. */
function RunFacts({
  run,
  totals,
  duration,
}: {
  run: Schema["RunView"];
  totals: RunTimeline["totals"];
  duration: number | null;
}) {
  const { t } = useTranslation();
  // Tokens nobody reported are unknown, never zero, so they are not shown.
  const figures =
    totals.reportedUsage &&
    tokenFigures(t, {
      input: totals.inputTokens,
      output: totals.outputTokens,
      cached: totals.cacheReadTokens,
    });
  return (
    <dl className={styles.facts}>
      <Fact label={t("Started")}>
        <StartTime at={run.started_at ?? run.created_at} />
      </Fact>
      <Fact label={t("Duration")}>{formatDuration(duration)}</Fact>
      <Fact label={t("Model calls")}>{totals.modelCalls}</Fact>
      {figures && (
        <Fact label={t("Tokens")}>
          <span title={figures.pairTitle}>{figures.pair}</span>
        </Fact>
      )}
      {figures && figures.rate && (
        <Fact label={t("Cache")}>
          <span title={figures.cacheTitle}>{figures.rate}</span>
        </Fact>
      )}
      {totals.costUsd && (
        <Fact label={t("Cost")}>{formatCost(totals.costUsd)}</Fact>
      )}
      <div className={`${styles.fact} ${styles.factId}`}>
        <dt>{t("Run ID")}</dt>
        <dd>
          <CopyableId value={run.id} />
        </dd>
      </div>
    </dl>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className={styles.fact}>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/**
 * What the Run was given or returned: a line of its log that holds the
 * content beneath it, and folds back to that line.
 */
function LogBlock({
  time,
  label,
  source,
  action,
  children,
}: {
  time: string;
  label: string;
  /** Where the content came from, read as a fact beside its label. */
  source?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(true);
  return (
    <section className={styles.block} aria-label={label}>
      <div className={styles.blockHead}>
        <span className={styles.time}>{time}</span>
        <span className={styles.lineBody}>
          <button
            type="button"
            className={styles.blockToggle}
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
          >
            <span className={styles.mark}>
              <CaretRightIcon
                size={11}
                className={styles.caret}
                data-expanded={open || undefined}
                aria-hidden="true"
              />
            </span>
            <span className={styles.blockLabel}>{label}</span>
          </button>
          {source && <span className={styles.blockSource}>{source}</span>}
          {action && <span className={styles.blockAction}>{action}</span>}
        </span>
      </div>
      {open && <div className={styles.blockBody}>{children}</div>}
    </section>
  );
}

/** What the Run returned: its text, or the structure its output spec asked for. */
function RunOutput({ output }: { output: Schema["RunView"]["output"] }) {
  const { t } = useTranslation();
  const [raw, setRaw] = useState(false);
  if (output == null || output === "") return null;
  if (typeof output !== "string")
    return (
      <LogBlock time="" label={t("Output")}>
        <JsonView value={output} />
      </LogBlock>
    );
  return (
    <LogBlock
      time=""
      label={t("Output")}
      action={
        <>
          {/* Rendered Markdown hides its own markup; Raw shows the text. */}
          <Button
            size="xs"
            variant="ghost"
            type="button"
            aria-pressed={raw}
            onClick={() => setRaw((value) => !value)}
          >
            {t("Raw")}
          </Button>
          <CopyButton value={output} iconOnly copyLabel={t("Copy output")} />
        </>
      }
    >
      <Clamp tall>
        {raw ? (
          <pre className={styles.raw}>{output}</pre>
        ) : (
          <MarkdownContent text={output} />
        )}
      </Clamp>
    </LogBlock>
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
    <>
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
    </>
  );
}

/** When the Run started, to the second: the date as well on another day. */
function StartTime({ at }: { at: string }) {
  const { i18n } = useTranslation();
  const date = new Date(at);
  const today = date.toDateString() === new Date().toDateString();
  const format = new Intl.DateTimeFormat(i18n?.resolvedLanguage, {
    ...(!today && { dateStyle: "medium" }),
    timeStyle: "medium",
  });
  return (
    <time dateTime={at} title={date.toLocaleString(i18n?.resolvedLanguage)}>
      {format.format(date)}
    </time>
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
