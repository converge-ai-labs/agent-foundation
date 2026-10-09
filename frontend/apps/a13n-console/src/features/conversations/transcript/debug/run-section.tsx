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
  const { t, i18n } = useTranslation();
  const { can, basePath } = useWorkspace();
  const [open, toggleOpen] = useRunOpen(run.id);
  const [details, setDetails] = useState(false);
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
        <ForkRun
          run={run}
          thread={thread}
          level="debug"
          index={index}
          compact
        />
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
        {request.kind === "message" ? (
          <MessageAuthor
            entryId={run.source_entry_id}
            principalId={run.principal_id}
          />
        ) : (
          <span className={styles.requestLabel}>
            {requestLabel(request, t)}
          </span>
        )}
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
                resubmit && (
                  <Button size="sm" variant="outline" onClick={resubmit}>
                    {t("Resubmit")}
                  </Button>
                )
              }
            />
          )}
          {run.output != null && typeof run.output !== "string" && (
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
function Lineage({ run }: { run: Schema["RunView"] }) {
  const { t } = useTranslation();
  const notes = [
    run.lineage === "fork" && t("fork"),
    run.attempts > 1 && t("{{count}} attempts", { count: run.attempts }),
  ].filter(Boolean) as string[];
  if (!notes.length) return null;
  return <span className={styles.lineage}>{notes.join(" · ")}</span>;
}
