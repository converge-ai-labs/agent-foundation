import { useState, type ReactNode } from "react";
import { useParams } from "react-router";
import { useTranslation } from "react-i18next";
import { CaretRightIcon, StackIcon } from "@phosphor-icons/react";
import type { Schema } from "../../../shared/api";
import styles from "./session-map.module.css";

type Run = Schema["RunResource"];

/** Keep branch points outside collapsible stretches so their child threads stay reachable. */
export function groupRuns(
  runs: readonly Run[],
  branchPoints: ReadonlySet<string>,
) {
  const groups: { runs: Run[]; start: number }[] = [];
  let pending: Run[] = [];
  let start = 1;
  runs.forEach((run, index) => {
    if (branchPoints.has(run.id)) {
      if (pending.length) groups.push({ runs: pending, start });
      groups.push({ runs: [run], start: index + 1 });
      pending = [];
      start = index + 2;
    } else {
      pending.push(run);
    }
  });
  if (pending.length) groups.push({ runs: pending, start });
  return groups;
}

export function RunGroup({
  runs,
  start,
  children,
}: {
  runs: readonly Run[];
  start: number;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const { runId } = useParams();
  const containsCurrent = runs.some((run) => run.id === runId);
  const [expanded, setExpanded] = useState(containsCurrent);
  const label = t("Runs {{start}}–{{end}}", {
    start,
    end: start + runs.length - 1,
  });
  return (
    <li className={styles.runNode}>
      <button
        className={styles.groupToggle}
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <CaretRightIcon
          size={12}
          className={styles.caret}
          data-expanded={expanded}
        />
        <StackIcon size={13} />
        <span>{label}</span>
        <span className={styles.count}>{runs.length}</span>
        {!expanded && containsCurrent && (
          <span
            className={styles.currentDot}
            aria-label={t("Contains current run")}
          />
        )}
      </button>
      {expanded && <ol className={styles.runs}>{children}</ol>}
    </li>
  );
}
