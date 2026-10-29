import { SortButton } from "../../../../shared/collection";
import { KnownSubtotal } from "../../../../shared/known-subtotal";
import { useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { CaretUpIcon, CaretDownIcon } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import type { Schema } from "../../../../shared/api";
import { costColumnFormat, exactCost } from "../../../../shared/cost";
import { ErrorNotice } from "../../../../shared/feedback";
import { formatDuration, resultExcerpt } from "../../format";
import type { OutlineRun, RunOutline as Outline } from "./outline";
import styles from "./run-outline.module.css";

/** The current Thread's Runs in the stage's left gutter on wide viewports. */
export function RunOutline({
  outline,
  compact = false,
  onSelect,
}: {
  outline: Outline;
  compact?: boolean;
  onSelect?: () => void;
}) {
  const { t } = useTranslation();
  const panel = useRef<HTMLElement>(null);
  const previousSort = useRef(outline.sort);
  useEffect(() => {
    if (
      previousSort.current.field !== outline.sort.field ||
      previousSort.current.direction !== outline.sort.direction
    ) {
      previousSort.current = outline.sort;
      if (panel.current) panel.current.scrollTop = 0;
      return;
    }
    const selected = panel.current?.querySelector("[aria-current]");
    if (selected && selected !== document.activeElement)
      selected.scrollIntoView({ block: "nearest" });
  }, [outline.inView, outline.sort.field, outline.sort.direction]);
  const cost = useMemo(
    () => costColumnFormat(outline.sorted.map((node) => node.cost)),
    [outline.sorted],
  );
  const sortButton = (field: Outline["sort"]["field"], label: string) => {
    const active = outline.sort.field === field;
    return (
      <SortButton
        label={label}
        className={styles.sortButton}
        direction={active ? outline.sort.direction : undefined}
        onClick={() =>
          outline.setSort({
            field,
            direction: active
              ? outline.sort.direction === "asc"
                ? "desc"
                : "asc"
              : field === "order"
                ? "asc"
                : "desc",
          })
        }
      />
    );
  };
  return (
    <nav
      className={styles.panel}
      data-compact={compact || undefined}
      aria-label={t("Runs")}
      ref={panel}
    >
      <div className={styles.panelHead}>
        <div className={styles.heading}>
          <span>
            {t("Runs")} <span className={styles.count}>{outline.total}</span>
          </span>
          {sortButton("order", t("Order"))}
        </div>
        <div className={styles.columns}>
          {sortButton("cost", t("Cost"))}
          {sortButton("calls", t("Model calls"))}
          {sortButton("duration", t("Duration"))}
        </div>
      </div>
      {outline.sort.field === "order" && (
        <RunPages
          outline={outline}
          direction={outline.sort.direction === "asc" ? "older" : "newer"}
        />
      )}
      <ErrorNotice error={outline.error} retry={outline.retry} />
      {outline.sort.field !== "order" && (
        <ErrorNotice
          error={outline.rankingError}
          retry={outline.retryRanking}
        />
      )}
      {outline.sorted.map((node) => (
        <button
          key={node.run.id}
          type="button"
          className={`${styles.row} ${styles.panelRow}`}
          aria-current={node.run.id === outline.inView || undefined}
          onClick={() => {
            outline.open(node.run);
            onSelect?.();
          }}
        >
          <RunRow node={node} cost={cost} />
        </button>
      ))}
      {outline.sort.field === "order" ? (
        <RunPages
          outline={outline}
          direction={outline.sort.direction === "asc" ? "newer" : "older"}
        />
      ) : (
        (outline.hasMore || outline.rankingLoading) && (
          <Button
            variant="ghost"
            size="sm"
            className={styles.pageAction}
            disabled={outline.rankingLoading}
            onClick={outline.loadMore}
          >
            {t(outline.rankingLoading ? "Loading…" : "Load more")}
          </Button>
        )
      )}
    </nav>
  );
}

/** The facts of one Run, the same in either presentation. */
export function RunRow({
  node,
  cost,
}: {
  node: OutlineRun;
  cost: (value: string | null) => string;
}) {
  const { t } = useTranslation();
  // The excerpt is the same resolved request the Run's own section shows.
  const excerpt = resultExcerpt(node.run.input_preview.text, 64);
  return (
    <>
      <span className={styles.identity}>
        <span
          className={styles.dot}
          data-state={node.run.status}
          aria-hidden="true"
        />
        <span className={styles.name}>{node.label}</span>
        <span className={styles.excerpt}>
          {excerpt || t("No request text")}
        </span>
      </span>
      <span className={styles.metrics}>
        <span
          className={styles.cost}
          title={
            node.unpriced
              ? t("Known subtotal; some model calls are unpriced.")
              : exactCost(node.cost)
          }
        >
          {cost(node.cost)}
          {node.cost !== null && node.unpriced && <KnownSubtotal />}
        </span>
        <span>{node.calls}</span>
        <span className={styles.duration}>{spanOf(node.run, t)}</span>
      </span>
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

function RunPages({
  outline,
  direction,
}: {
  outline: Outline;
  direction: "older" | "newer";
}) {
  const { t } = useTranslation();
  const older = direction === "older";
  if (!(older ? outline.hasOlder : outline.hasNewer)) return null;
  return (
    <Button
      type="button"
      size="sm"
      variant="ghost"
      className={styles.pageAction}
      disabled={outline.loading}
      onClick={older ? outline.loadOlder : outline.loadNewer}
    >
      {older ? (
        <CaretUpIcon aria-hidden="true" />
      ) : (
        <CaretDownIcon aria-hidden="true" />
      )}
      {t(older ? "Earlier runs" : "Later runs")}
    </Button>
  );
}
