import { Button } from "a13n-ui";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  ArrowsDownUpIcon,
} from "@phosphor-icons/react";
import { Link, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { ResourceTable, type ResourceColumn } from "../../shared/collection";
import { CopyButton, Identifier, IconTile } from "../../shared/identity";
import { Timestamp } from "../../shared/feedback";
import {
  compareObservations,
  compareValues,
  type ObservationSort,
} from "./sorting";
import { formatCost } from "../../shared/cost";
import { ObservationGlyph } from "./identity";
import { Duration, TracePill } from "./values";
import styles from "./list.module.css";

const sortable = [
  { field: "duration", label: "Duration" },
  { field: "cost", label: "Cost" },
  { field: "started", label: "Started" },
] as const;

type SortField = (typeof sortable)[number]["field"];

export function TraceTable({
  items,
  basePath,
  costs,
  sort,
  onSortChange,
}: {
  items: readonly Schema["Trace"][];
  basePath: string;
  costs: Readonly<Record<string, string | null>>;
  sort: ObservationSort;
  onSortChange: (sort: ObservationSort) => void;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const sorted = [...items].sort((a, b) =>
    sort.field === "cost"
      ? compareValues(costs[a.id] ?? null, costs[b.id] ?? null, sort.direction)
      : compareObservations(a.root, b.root, sort),
  );
  const detailUrl = (id: string) =>
    `${basePath}/traces/${encodeURIComponent(id)}`;
  const pageSortHint = t("Sorts the traces on this page only.");
  const sortHeader = (field: SortField, label: string) => {
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
        data-active={active || undefined}
        title={pageSortHint}
        onClick={() =>
          onSortChange({
            field,
            direction: active && sort.direction === "desc" ? "asc" : "desc",
          })
        }
      >
        {label}
        <Icon size={12} aria-hidden="true" />
      </Button>
    );
  };
  const columns: ResourceColumn<Schema["Trace"]>[] = [
    {
      label: t("Trace"),
      tone: "primary",
      render: (item) => (
        <div className={styles.traceIdentity}>
          <IconTile size={32}>
            <ObservationGlyph observation={item.root} />
          </IconTile>
          <div className={styles.traceCopy}>
            <Link className={styles.traceName} to={detailUrl(item.id)}>
              {item.root.name}
            </Link>
            <span className={styles.traceMeta}>
              <Identifier value={item.id} />
              <CopyButton
                value={item.id}
                iconOnly
                copyLabel={t("Copy trace ID")}
              />
            </span>
          </div>
        </div>
      ),
    },
    {
      label: t("Run"),
      dataColumn: "run",
      render: (item) => (
        <Link
          className={styles.runLink}
          title={`${t("Run ID")}: ${item.correlation.run_id}`}
          to={`${basePath}/sessions/${item.correlation.session_id}/threads/${item.correlation.thread_id}/runs/${item.correlation.run_id}`}
        >
          {item.correlation.run_id}
        </Link>
      ),
    },
    {
      label: t("Level"),
      dataColumn: "level",
      render: (item) => <TracePill level={item.root.level} />,
    },
    ...sortable.map(({ field, label }): ResourceColumn<Schema["Trace"]> => {
      const translated = t(label);
      return {
        label: translated,
        // Numbers line up at the right; a timestamp reads from the left.
        align: field === "started" ? "left" : "right",
        dataColumn: field,
        ariaSort:
          sort.field === field
            ? sort.direction === "asc"
              ? "ascending"
              : "descending"
            : "none",
        header: sortHeader(field, translated),
        render: (item) =>
          field === "duration" ? (
            <Duration observation={item.root} />
          ) : field === "cost" ? (
            <span
              title={t(
                "Reported observation costs; missing costs are not estimated.",
              )}
            >
              {formatCost(costs[item.id] ?? null)}
            </span>
          ) : (
            <Timestamp value={item.root.started_at} />
          ),
      };
    }),
  ];
  return (
    <ResourceTable
      items={sorted}
      columns={columns}
      caption={t("Traces")}
      className={styles.traceTable}
      onRowActivate={(item) => navigate(detailUrl(item.id))}
    />
  );
}
