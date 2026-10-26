import { Button } from "a13n-ui";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  ArrowsDownUpIcon,
} from "@phosphor-icons/react";
import { Link, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { ResourceTable } from "../../shared/collection";
import { CopyButton, Identifier } from "../../shared/identity";
import { InlineLoading, Timestamp } from "../../shared/feedback";
import { UNKNOWN } from "../../shared/unknown";
import { type ObservationSort } from "./sorting";
import { costColumnFormat } from "../../shared/cost";
import { runPath, traceCorrelation } from "./correlation";
import { Duration, LevelPill } from "./values";
import styles from "./list.module.css";

export function TraceTable({
  items,
  basePath,
  costs,
  sort,
  onSortChange,
}: {
  /** Trace roots, one per attempt. */
  items: readonly Schema["Span"][];
  basePath: string;
  /** Reported cost per trace; undefined while the costs load. */
  costs: Readonly<Record<string, string | null>> | undefined;
  sort: ObservationSort;
  onSortChange: (sort: ObservationSort) => void;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const cost = costColumnFormat(Object.values(costs ?? {}));
  const detailUrl = (id: string) =>
    `${basePath}/traces/${encodeURIComponent(id)}`;
  const sortHeader = (field: ObservationSort["field"], label: string) => {
    const active = sort.field === field;
    const Icon = !active
      ? ArrowsDownUpIcon
      : sort.direction === "asc"
        ? ArrowUpIcon
        : ArrowDownIcon;
    return (
      <Button
        variant="ghost"
        size="sm"
        className={styles.sortButton}
        title={t("Sorts all matching traces in the selected time range.")}
        onClick={() =>
          onSortChange({
            field,
            direction: active && sort.direction === "desc" ? "asc" : "desc",
          })
        }
      >
        {t(label)}
        <Icon size={12} aria-hidden="true" />
      </Button>
    );
  };
  const ariaSort = (field: ObservationSort["field"]) =>
    sort.field !== field
      ? ("none" as const)
      : sort.direction === "asc"
        ? ("ascending" as const)
        : ("descending" as const);
  return (
    <ResourceTable
      items={items}
      caption={t("Traces")}
      className={styles.traceTable}
      onRowActivate={(item) => navigate(detailUrl(item.trace_id))}
      columns={[
        {
          label: t("Trace"),
          tone: "primary",
          render: (item) => (
            <div className={styles.traceCopy}>
              <Link className={styles.traceName} to={detailUrl(item.trace_id)}>
                {item.name}
              </Link>
              <span className={styles.copyable}>
                <Identifier value={item.trace_id} />
                <CopyButton
                  value={item.trace_id}
                  iconOnly
                  copyLabel={t("Copy trace ID")}
                />
              </span>
            </div>
          ),
        },
        {
          label: t("Run"),
          dataColumn: "run",
          render: (item) => <RunLink root={item} basePath={basePath} />,
        },
        {
          label: t("Level"),
          dataColumn: "level",
          render: (item) => <LevelPill level={item.level} />,
        },
        {
          label: t("Duration"),
          align: "right",
          dataColumn: "duration",
          ariaSort: ariaSort("duration"),
          header: sortHeader("duration", "Duration"),
          render: (item) => <Duration observation={item} />,
        },
        {
          label: t("Cost"),
          align: "right",
          dataColumn: "cost",
          ariaSort: ariaSort("cost"),
          header: sortHeader("cost", "Cost"),
          render: (item) =>
            costs ? (
              <span
                title={t(
                  "Reported observation costs; missing costs are not estimated.",
                )}
              >
                {cost(costs[item.trace_id] ?? null)}
              </span>
            ) : (
              <InlineLoading width="44px" />
            ),
        },
        {
          label: t("Started"),
          dataColumn: "started",
          ariaSort: ariaSort("started"),
          header: sortHeader("started", "Started"),
          render: (item) => <Timestamp value={item.started_at} />,
        },
      ]}
    />
  );
}

/** `run_e9cc…bfc1`: the prefix and both ends of an ID tell rows apart. */
function shortId(id: string) {
  const prefix = id.indexOf("_") + 1;
  return id.length - prefix > 10
    ? `${id.slice(0, prefix + 4)}…${id.slice(-4)}`
    : id;
}

/** The traced run; without its session and thread it reads as its ID alone. */
function RunLink({
  root,
  basePath,
}: {
  root: Schema["Span"];
  basePath: string;
}) {
  const { t } = useTranslation();
  const correlation = traceCorrelation(root),
    runId = correlation.run_id,
    path = runPath(basePath, correlation);
  if (!runId) return <>{UNKNOWN}</>;
  return (
    <span className={styles.copyable}>
      {path ? (
        <Link className={styles.runLink} title={runId} to={path}>
          {shortId(runId)}
        </Link>
      ) : (
        <span className={styles.runLink} title={runId}>
          {shortId(runId)}
        </span>
      )}
      <CopyButton value={runId} iconOnly copyLabel={t("Copy run ID")} />
    </span>
  );
}
