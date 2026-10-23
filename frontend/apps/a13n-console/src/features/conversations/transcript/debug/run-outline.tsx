import { useEffect, useRef } from "react";
import { Button, MenuItem } from "a13n-ui";
import { ErrorNotice } from "../../../../shared/feedback";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../../../shared/api";
import { formatDuration, resultExcerpt } from "../../format";
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
      <div className={styles.panelHead} aria-hidden="true">
        <span>{t("Runs")}</span>
        <span>{outline.total}</span>
      </div>
      <RunPages outline={outline} direction="older" />
      <ErrorNotice error={outline.error} retry={outline.retry} />
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
      <RunPages outline={outline} direction="newer" />
    </nav>
  );
}

/** The facts of one Run, the same in either presentation. */
export function RunRow({ node }: { node: OutlineRun }) {
  const { t } = useTranslation();
  // The excerpt is the same resolved request the Run's own section shows.
  const excerpt = resultExcerpt(node.run.input_preview.text, 64);
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
  run: Schema["RunListItem"],
  t: (key: string, options?: Record<string, unknown>) => string,
) {
  return run.started_at && run.sealed_at && run.status !== "waiting"
    ? formatDuration(Date.parse(run.sealed_at) - Date.parse(run.started_at))
    : t(`state.${run.status}`, { defaultValue: run.status });
}

/** Page actions are shared by the outline and the narrow-screen menu. */
export function RunPages({
  outline,
  direction,
  menu = false,
}: {
  outline: Outline;
  direction: "older" | "newer";
  menu?: boolean;
}) {
  const { t } = useTranslation();
  const older = direction === "older";
  if (!(older ? outline.hasOlder : outline.hasNewer)) return null;
  if (menu)
    return (
      <MenuItem
        closeOnClick={false}
        disabled={outline.loading}
        onClick={older ? outline.loadOlder : outline.loadNewer}
      >
        {t(older ? "Load earlier runs" : "Load newer runs")}
      </MenuItem>
    );
  return (
    <Button
      type="button"
      size="sm"
      variant="ghost"
      disabled={outline.loading}
      onClick={older ? outline.loadOlder : outline.loadNewer}
    >
      {t(older ? "Load earlier runs" : "Load newer runs")}
    </Button>
  );
}
