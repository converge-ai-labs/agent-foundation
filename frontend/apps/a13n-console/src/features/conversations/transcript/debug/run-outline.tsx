import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../../../shared/api";
import { formatDuration, resultExcerpt } from "../../format";
import { runRequest } from "../../request";
import type { OutlineRun, RunOutline as Outline } from "./outline";
import styles from "./run-outline.module.css";

/** The current Thread's Runs in the stage's left gutter on wide viewports. */
export function RunOutline({ outline }: { outline: Outline }) {
  const { t } = useTranslation();
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    panel.current
      ?.querySelector("[aria-current]")
      ?.scrollIntoView({ block: "nearest" });
  }, [outline.inView]);
  return (
    <nav className={styles.panel} aria-label={t("Runs")} ref={panel}>
      {outline.nodes.map((node) => (
        <button
          key={node.run.id}
          type="button"
          className={`${styles.row} ${styles.panelRow}`}
          aria-current={node.run.id === outline.inView || undefined}
          onClick={() => outline.open(node.run)}
        >
          <RunRow node={node} />
        </button>
      ))}
    </nav>
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

/** How long it took, or where it stopped: a waiting Run is sealed, not done. */
function spanOf(
  run: Schema["RunView"],
  t: (key: string, options?: Record<string, unknown>) => string,
) {
  return run.started_at && run.sealed_at && run.status !== "waiting"
    ? formatDuration(Date.parse(run.sealed_at) - Date.parse(run.started_at))
    : t(`state.${run.status}`, { defaultValue: run.status });
}
