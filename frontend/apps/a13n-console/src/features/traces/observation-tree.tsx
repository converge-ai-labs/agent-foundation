import { CaretRightIcon } from "@phosphor-icons/react";
import {
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
} from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { isFailed, observationKind, ObservationIcon } from "./identity";
import { visibleRows, type TimelineRow } from "./timeline";
import { Duration, durationMs } from "./values";
import styles from "./traces.module.css";

/** Seconds label for the timeline scale; every displayed duration uses seconds. */
function seconds(value: number) {
  return `${(value / 1000).toLocaleString(undefined, { maximumFractionDigits: 6 })} s`;
}

/**
 * The observation timeline: one row per loaded observation, a waterfall track
 * shared with the run timeline, and tree navigation by keyboard. Collapse state
 * belongs to the mounted trace and defaults to fully expanded.
 */
export function ObservationTree({
  rows,
  tree,
  start,
  duration,
  loaded,
  selectedId,
  onSelect,
}: {
  rows: readonly TimelineRow[];
  /** Call-tree order carries carets, indentation and levels; sorts do not. */
  tree: boolean;
  start: number;
  duration: number;
  loaded: ReadonlySet<string>;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const elements = useRef(new Map<string, HTMLDivElement>());
  const visible = tree ? visibleRows(rows, collapsed) : rows;
  const has = (id: string | null) =>
    visible.some((row) => row.observation.id === id);
  // One row at a time takes the tab stop, so the timeline is one keyboard stop.
  const tabbableId = has(focusedId)
    ? focusedId
    : has(selectedId)
      ? selectedId
      : visible[0]?.observation.id;
  const focus = (id: string) => {
    setFocusedId(id);
    elements.current.get(id)?.focus();
  };
  const toggle = (id: string) =>
    setCollapsed((current) => {
      const next = new Set(current);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>, index: number) {
    const row = visible[index];
    if (!row) return;
    const id = row.observation.id;
    const expandable = tree && row.childCount > 0;
    const open = expandable && !collapsed.has(id);
    const step = (target: number) => {
      const next = visible[target];
      if (next) focus(next.observation.id);
    };
    switch (event.key) {
      case "ArrowDown":
        step(index + 1);
        break;
      case "ArrowUp":
        step(index - 1);
        break;
      case "Home":
        step(0);
        break;
      case "End":
        step(visible.length - 1);
        break;
      case "ArrowRight":
        if (!expandable) return;
        if (open) step(index + 1);
        else toggle(id);
        break;
      case "ArrowLeft":
        if (open) toggle(id);
        else
          for (let above = index - 1; above >= 0; above--)
            if (visible[above]!.depth < row.depth) {
              focus(visible[above]!.observation.id);
              break;
            }
        break;
      case "Enter":
      case " ":
        focus(id);
        onSelect(id);
        break;
      default:
        return;
    }
    event.preventDefault();
  }
  return (
    <div className={styles.timeline}>
      <div className={styles.timelineHeader}>
        <span>{t("Observations")}</span>
        <div className={styles.timelineScale} aria-hidden="true">
          <span>0 s</span>
          <span>{seconds(duration)}</span>
        </div>
        <span className={styles.timelineDurationLabel}>{t("Duration")}</span>
      </div>
      <div
        className={styles.timelineRows}
        role="tree"
        aria-label={t("Observations")}
      >
        {visible.map((row, index) => {
          const id = row.observation.id;
          const expandable = tree && row.childCount > 0;
          return (
            <div
              key={id}
              ref={(node) => {
                if (node) elements.current.set(id, node);
                else elements.current.delete(id);
              }}
              role="treeitem"
              aria-level={tree ? row.depth + 1 : 1}
              aria-selected={selectedId === id}
              aria-expanded={expandable ? !collapsed.has(id) : undefined}
              tabIndex={tabbableId === id ? 0 : -1}
              className={styles.observation}
              data-selected={selectedId === id || undefined}
              style={
                {
                  "--depth": tree ? Math.min(row.depth, 12) : 0,
                } as CSSProperties
              }
              onClick={() => {
                setFocusedId(id);
                onSelect(id);
              }}
              onKeyDown={(event) => onKeyDown(event, index)}
            >
              <ObservationRow
                row={row}
                tree={tree}
                expandable={expandable}
                open={expandable && !collapsed.has(id)}
                loaded={loaded}
                onToggle={() => {
                  setFocusedId(id);
                  toggle(id);
                }}
              />
              <span className={styles.track} aria-hidden="true">
                <Bar
                  observation={row.observation}
                  start={start}
                  duration={duration}
                />
              </span>
              <span className={styles.duration}>
                <Duration observation={row.observation} />
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Identity cell: caret, kind tile, and `name · type · model` on one line. */
function ObservationRow({
  row,
  tree,
  expandable,
  open,
  loaded,
  onToggle,
}: {
  row: TimelineRow;
  tree: boolean;
  expandable: boolean;
  open: boolean;
  loaded: ReadonlySet<string>;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const { observation, childCount } = row;
  const model = observation.model?.response ?? observation.model?.requested;
  const orphan =
    observation.parent_id !== null && !loaded.has(observation.parent_id);
  const meta = [observation.type, model, orphan && t("Parent not loaded")]
    .filter(Boolean)
    .join(" · ");
  return (
    <span className={styles.observationName}>
      {tree &&
        (expandable ? (
          <span
            aria-hidden="true"
            className={styles.caret}
            data-open={open || undefined}
            onClick={(event) => {
              event.stopPropagation();
              onToggle();
            }}
          >
            <CaretRightIcon size={11} weight="bold" />
          </span>
        ) : (
          <span aria-hidden="true" className={styles.caret} />
        ))}
      <ObservationIcon observation={observation} />
      <span className={styles.observationCopy}>
        <span className={styles.observationTitle} title={observation.name}>
          {observation.name}
        </span>
        <span className={styles.observationMeta} title={meta}>
          {meta}
        </span>
        {expandable && !open && (
          <span className={styles.childCount}>
            {t("{{count}} nested", { count: childCount })}
          </span>
        )}
      </span>
    </span>
  );
}

/** Waterfall bar: start offset and width relative to the loaded trace window. */
function Bar({
  observation,
  start,
  duration,
}: {
  observation: Schema["Observation"];
  start: number;
  duration: number;
}) {
  const elapsed = durationMs(observation);
  if (elapsed === null) return null;
  const left = Math.max(
    0,
    Math.min(
      100,
      ((Date.parse(observation.started_at) - start) / duration) * 100,
    ),
  );
  const width = Math.max(0.5, Math.min(100 - left, (elapsed / duration) * 100));
  return (
    <span
      className={styles.bar}
      style={{ left: `${left}%`, width: `${width}%` }}
      data-kind={observationKind(observation)}
      data-failed={isFailed(observation) || undefined}
    />
  );
}
