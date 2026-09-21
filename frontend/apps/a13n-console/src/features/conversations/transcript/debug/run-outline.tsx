import { Fragment, useEffect, useRef, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../../../shared/api";
import { formatDuration, resultExcerpt } from "../../format";
import { runRequest } from "../../request";
import type {
  OutlineGroup,
  OutlineRun,
  RunOutline as Outline,
} from "./outline";
import styles from "./run-outline.module.css";

/**
 * The outline: the whole tree of Runs in the stage's left gutter, where the
 * page is wide enough to have one. Narrower viewports read the same tree from
 * the pinned pill's list instead.
 */
export function RunOutline({ outline }: { outline: Outline }) {
  const { t } = useTranslation();
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    panel.current
      ?.querySelector("[aria-current]")
      ?.scrollIntoView({ block: "nearest" });
  }, [outline.inView]);
  const named = hasNestedGroups(outline.groups);
  return (
    <nav className={styles.panel} aria-label={t("Runs")} ref={panel}>
      {outline.groups.map((group) => (
        <OutlineBranch
          key={group.thread.id}
          group={group}
          outline={outline}
          labelled={named}
          row={(node, current) => (
            <button
              type="button"
              className={`${styles.row} ${styles.panelRow}`}
              aria-current={current || undefined}
              onClick={() => outline.open(node.run)}
            >
              <RunRow node={node} />
            </button>
          )}
        />
      ))}
    </nav>
  );
}

/** Whether the tree branches at all, which is when its groups need naming. */
export function hasNestedGroups(groups: readonly OutlineGroup[]): boolean {
  return groups.some((group) =>
    group.runs.some((node) => node.groups.length > 0),
  );
}

/**
 * One Thread's Runs, with the Threads that branched from them beneath. Each
 * presentation supplies the element one Run row is.
 */
export function OutlineBranch({
  group,
  outline,
  row,
  labelled = true,
  nested = false,
}: {
  group: OutlineGroup;
  outline: Outline;
  row: (node: OutlineRun, current: boolean) => ReactNode;
  labelled?: boolean;
  nested?: boolean;
}) {
  return (
    <div className={styles.group} data-nested={nested || undefined}>
      {labelled && <span className={styles.groupLabel}>{group.label}</span>}
      {group.runs.map((node) => (
        <Fragment key={node.run.id}>
          {row(node, node.run.id === outline.inView)}
          {node.groups.map((child) => (
            <OutlineBranch
              key={child.thread.id}
              group={child}
              outline={outline}
              row={row}
              nested
            />
          ))}
        </Fragment>
      ))}
    </div>
  );
}

/** The facts of one Run, the same in either presentation. */
export function RunRow({ node }: { node: OutlineRun }) {
  const { t } = useTranslation();
  // The excerpt is the same resolved request the Run's own section shows.
  const excerpt = resultExcerpt(runRequest(node.run, node.thread).text, 64);
  return (
    <>
      <span
        className={styles.dot}
        data-state={node.run.status}
        aria-hidden="true"
      />
      <span className={styles.name}>{node.label}</span>
      <span className={styles.excerpt}>{excerpt || t("No request text")}</span>
      <span className={styles.meta}>{spanOf(node.run, t)}</span>
    </>
  );
}

/** How long it took, or where it stopped. */
function spanOf(
  run: Schema["RunResource"],
  t: (key: string, options?: Record<string, unknown>) => string,
) {
  return run.started_at && run.completed_at
    ? formatDuration(Date.parse(run.completed_at) - Date.parse(run.started_at))
    : t(`state.${run.status}`, { defaultValue: run.status });
}
