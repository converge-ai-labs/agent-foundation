import { Button, DisclosureSection } from "a13n-ui";
import { CaretRightIcon } from "@phosphor-icons/react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../../../layout/workspace";
import type { Schema } from "../../../../shared/api";
import { StatePill } from "../../../../shared/feedback";
import { JsonView } from "../../../../shared/forms";
import { isActiveRun } from "../../api";
import { formatCost } from "../../../../shared/cost";
import { formatDuration, formatTokens } from "../../format";
import { runOutcome } from "../../lifecycle";
import { runRequest } from "../../request";
import type { RunTimeline } from "../../timeline";
import { RequestContent, requestLabel } from "../user-message";
import { isInteractive, useRetryRun, useRunAcceptance } from "../run-actions";
import {
  childThreadOf,
  childThreadPath,
  useChildThreads,
} from "../thread-runs";
import { useRunOpen } from "./collapse";
import { RunDetails } from "./run-details";
import { EventRow, TimelineRows } from "./timeline-rows";
import { useNow } from "./use-now";
import type { RunScope } from "./scope";
import styles from "./debug.module.css";

/** One Run, read as a section: what was asked, what ran, and what it cost. */
export function DebugRunSection({
  run,
  thread,
  timeline,
  index,
  runNumber,
}: {
  run: Schema["RunResource"];
  thread: Schema["ThreadResource"];
  /** The same reading of the Run the Chat level renders. */
  timeline: RunTimeline;
  /** Position in the Thread; omitted while the Thread's Runs are unknown. */
  index: number | null;
  /** Position of any Run of this Thread, for lineage notes. */
  runNumber?: (runId: string) => number | null;
}) {
  const { t, i18n } = useTranslation();
  const { can, basePath } = useWorkspace();
  const [open, toggleOpen] = useRunOpen(run.id);
  const [details, setDetails] = useState(false);
  const active = isActiveRun(run.status) || run.status === "waiting";
  const now = useNow(active);
  const children = useChildThreads(run.session_id, run.thread_id);
  const child = childThreadOf(children, run.id);
  const { accepted, refresh } = useRunAcceptance(run, thread);
  const retry = useRetryRun(run, thread, accepted, refresh);
  const started = Date.parse(run.started_at ?? run.created_at);
  const ended = run.completed_at ? Date.parse(run.completed_at) : now;
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
    answerable: isInteractive(thread) && can("run.feedback"),
    jumpToDock() {
      const stage = document.querySelector("[data-session-stage]");
      if (stage instanceof HTMLElement)
        stage.scrollTo({ top: stage.scrollHeight, behavior: "smooth" });
    },
  };
  const outcome = runOutcome(run);
  const request = runRequest(run, thread);
  return (
    <section className={styles.section} data-run={run.id}>
      <header className={styles.heading}>
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
        <Lineage
          run={run}
          attempts={attemptCount(timeline)}
          runNumber={runNumber}
        />
        <span className={styles.headingMeta}>
          <span>
            {new Intl.DateTimeFormat(i18n.resolvedLanguage, {
              timeStyle: "medium",
            }).format(new Date(run.started_at ?? run.created_at))}
          </span>
          <span className={styles.headingDuration}>
            {formatDuration(duration)}
          </span>
          <span>
            {t("{{count}} model calls", { count: timeline.totals.modelCalls })}
          </span>
          <span>
            {/* Tokens nobody reported are unknown, never zero. */}
            {timeline.totals.reportedUsage
              ? `${formatTokens(timeline.totals.inputTokens)} → ${formatTokens(timeline.totals.outputTokens)}`
              : `${formatTokens(null)} → ${formatTokens(null)}`}
          </span>
          {timeline.totals.costUsd && (
            <span>{formatCost(timeline.totals.costUsd)}</span>
          )}
        </span>
        <Button
          variant="ghost"
          size="sm"
          className={styles.detailsToggle}
          aria-expanded={details}
          onClick={() => setDetails((value) => !value)}
        >
          {t("Details")}
        </Button>
      </header>
      <div className={styles.request} data-collapsed={!open || undefined}>
        <span className={styles.requestLabel}>{requestLabel(request, t)}</span>
        <div className={styles.requestBody}>
          <RequestContent request={request} />
        </div>
      </div>
      {open && (
        <>
          {details && <RunDetails runId={run.id} />}
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
                ["failed", "cancelled"].includes(run.status) &&
                isInteractive(thread) &&
                can("run.retry") && (
                  <Button
                    size="sm"
                    variant="outline"
                    loading={retry.isPending}
                    onClick={() => retry.mutate()}
                  >
                    {t("Retry run")}
                  </Button>
                )
              }
            />
          )}
          {run.output != null && run.output !== run.output_text && (
            <DisclosureSection
              className={styles.structured}
              title={<>{t("Structured output")}</>}
            >
              <JsonView value={run.output} />
            </DisclosureSection>
          )}
        </>
      )}
    </section>
  );
}

/** Where this Run came from, when it was not simply the next one. */
function Lineage({
  run,
  attempts,
  runNumber,
}: {
  run: Schema["RunResource"];
  attempts: number;
  runNumber?: (runId: string) => number | null;
}) {
  const { t } = useTranslation();
  const source = run.retry_of_run_id
    ? (runNumber?.(run.retry_of_run_id) ?? null)
    : null;
  const notes = [
    run.retry_of_run_id &&
      (source === null
        ? t("retry")
        : t("retry of Run {{index}}", { index: source })),
    run.lineage_kind === "fork" && t("fork"),
    attempts > 1 && t("{{count}} attempts", { count: attempts }),
  ].filter(Boolean) as string[];
  if (!notes.length) return null;
  return <span className={styles.lineage}>{notes.join(" · ")}</span>;
}

/**
 * How many attempts the Run reported. Attempts are read from observed
 * lifecycle facts: the first one earns no row, so the highest number observed
 * is the count, and a Run that reported none ran once.
 */
function attemptCount(timeline: RunTimeline) {
  return timeline.entries.reduce(
    (count, entry) =>
      entry.kind === "event" && entry.type.startsWith("run_attempt.")
        ? Math.max(count, entry.attempt ?? 1)
        : count,
    1,
  );
}
