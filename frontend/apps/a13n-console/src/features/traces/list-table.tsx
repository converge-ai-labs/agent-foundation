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
import { ResourceReference } from "../../shared/identity";
import { Timestamp } from "../../shared/feedback";
import {
  compareObservations,
  compareValues,
  type ObservationSort,
} from "./sorting";
import { contentPreview } from "./preview";
import { formatCost } from "./cost";
import { Duration, Level } from "./values";
import { MetadataChips } from "./metadata";
import styles from "./traces.module.css";

export function TraceTable({
  items,
  basePath,
  view,
  costs,
  sort,
  onSortChange,
}: {
  items: readonly Schema["Trace"][];
  basePath: string;
  view: Schema["TraceView"];
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
  const sortable = [
    { field: "started", label: "Started", align: "left" },
    { field: "duration", label: "Duration", align: "right" },
    { field: "cost", label: "Cost", align: "right" },
  ] as const;
  const detailUrl = (id: string) =>
    `${basePath}/traces/${encodeURIComponent(id)}`;
  const sortHeader = (field: (typeof sortable)[number]["field"]) => (
    <Button
      variant="ghost"
      size="sm"
      className={styles.sortButton}
      onClick={() =>
        onSortChange({
          field,
          direction:
            sort.field === field && sort.direction === "desc" ? "asc" : "desc",
        })
      }
    >
      {t(sortable.find((column) => column.field === field)?.label ?? field)}{" "}
      {sort.field !== field ? (
        <ArrowsDownUpIcon size={13} />
      ) : sort.direction === "asc" ? (
        <ArrowUpIcon size={13} />
      ) : (
        <ArrowDownIcon size={13} />
      )}
    </Button>
  );
  const columns: ResourceColumn<Schema["Trace"]>[] = [
    {
      label: t("Trace"),
      render: (item) => (
        <div className={styles.traceIdentity}>
          <div className={styles.traceTitle}>
            <Link className={styles.traceName} to={detailUrl(item.id)}>
              {item.root.name}
            </Link>
            <ResourceReference
              id={item.id}
              idLabel={t("Trace ID")}
              idCopyLabel={t("Copy trace ID")}
              references={[
                {
                  label: t("Session ID"),
                  value: item.correlation.session_id,
                  copyLabel: t("Copy session ID"),
                },
                {
                  label: t("Thread ID"),
                  value: item.correlation.thread_id,
                  copyLabel: t("Copy thread ID"),
                },
                {
                  label: t("Run ID"),
                  value: item.correlation.run_id,
                  copyLabel: t("Copy run ID"),
                },
              ]}
            />
          </div>
          <div className={styles.identityLabels}>
            <Level level={item.root.level} />
            <MetadataChips observation={item.root} />
          </div>
        </div>
      ),
    },
    ...sortable.map(
      ({ field, label, align }): ResourceColumn<Schema["Trace"]> => ({
        label: t(label),
        align,
        dataColumn: field,
        ariaSort:
          sort.field === field
            ? sort.direction === "asc"
              ? "ascending"
              : "descending"
            : "none",
        header: sortHeader(field),
        render: (item) =>
          field === "started" ? (
            <Timestamp value={item.root.started_at} />
          ) : field === "duration" ? (
            <Duration observation={item.root} />
          ) : (
            <span
              title={t(
                "Reported observation costs; missing costs are not estimated.",
              )}
            >
              {formatCost(costs[item.id] ?? null)}
            </span>
          ),
      }),
    ),
    ...(view === "full"
      ? (["input", "output"] as const).map(
          (key): ResourceColumn<Schema["Trace"]> => ({
            label: t(key === "input" ? "Input" : "Output"),
            render: (item) =>
              item.root[key] === null ? (
                <span className={styles.missing}>-</span>
              ) : (
                <Link
                  to={`${detailUrl(item.id)}?tab=content`}
                  className={styles.listPreview}
                >
                  {contentPreview(item.root[key])}
                </Link>
              ),
          }),
        )
      : []),
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
