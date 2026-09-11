import {
  Button,
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "a13n-ui";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  ArrowsDownUpIcon,
} from "@phosphor-icons/react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { CopyableId } from "../../shared/copy";
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
  const sorted = [...items].sort((a, b) =>
    sort.field === "cost"
      ? compareValues(costs[a.id] ?? null, costs[b.id] ?? null, sort.direction)
      : compareObservations(a.root, b.root, sort),
  );
  const columns = [
    { field: "started", label: "Started" },
    { field: "duration", label: "Duration" },
    { field: "cost", label: "Cost" },
  ] as const;
  return (
    <>
      <p className={styles.providerNote}>
        {t("Sort applies to this page only. Pages are fetched newest first.")}
      </p>
      <Table className={styles.traceTable}>
        <TableCaption className="sr-only">{t("Traces")}</TableCaption>
        <TableHeader>
          <TableRow>
            <TableHead>{t("Trace")}</TableHead>
            {columns.map(({ field, label }) => (
              <TableHead
                key={field}
                data-column={field}
                className={field === "started" ? undefined : "text-right"}
                aria-sort={
                  sort.field === field
                    ? sort.direction === "asc"
                      ? "ascending"
                      : "descending"
                    : "none"
                }
              >
                <Button
                  variant="ghost"
                  size="sm"
                  className={styles.sortButton}
                  onClick={() =>
                    onSortChange({
                      field,
                      direction:
                        sort.field === field && sort.direction === "desc"
                          ? "asc"
                          : "desc",
                    })
                  }
                >
                  {t(label)}{" "}
                  {sort.field !== field ? (
                    <ArrowsDownUpIcon size={13} />
                  ) : sort.direction === "asc" ? (
                    <ArrowUpIcon size={13} />
                  ) : (
                    <ArrowDownIcon size={13} />
                  )}
                </Button>
              </TableHead>
            ))}
            {view === "full" && (
              <>
                <TableHead>{t("Input")}</TableHead>
                <TableHead>{t("Output")}</TableHead>
              </>
            )}
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((item) => (
            <TableRow key={item.id}>
              <TableCell className={styles.traceIdentity}>
                <Link
                  className={styles.traceName}
                  to={`${basePath}/traces/${encodeURIComponent(item.id)}`}
                >
                  {item.root.name}
                </Link>
                <CopyableId value={item.id} />
                <div className={styles.identityLabels}>
                  <Level level={item.root.level} />
                  <MetadataChips observation={item.root} />
                </div>
              </TableCell>
              <TableCell className={styles.started}>
                <Timestamp value={item.root.started_at} />
              </TableCell>
              <TableCell className={styles.numeric}>
                <Duration observation={item.root} />
              </TableCell>
              <TableCell className={styles.numeric}>
                <span
                  title={t(
                    "Reported observation costs; missing costs are not estimated.",
                  )}
                >
                  {formatCost(costs[item.id] ?? null)}
                </span>
              </TableCell>
              {view === "full" &&
                (["input", "output"] as const).map((key) => (
                  <TableCell key={key}>
                    {item.root[key] === null ? (
                      <span className={styles.missing}>-</span>
                    ) : (
                      <Link
                        to={`${basePath}/traces/${encodeURIComponent(item.id)}?tab=content`}
                        className={styles.listPreview}
                      >
                        {contentPreview(item.root[key])}
                      </Link>
                    )}
                  </TableCell>
                ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </>
  );
}
