import {
  BrandIcon,
  Button,
  Input,
  Popover,
  PopoverPopup,
  PopoverTrigger,
} from "a13n-ui";
import { CaretDownIcon, PlusIcon, XIcon } from "@phosphor-icons/react";
import {
  formatLocalDateTime,
  parseLocalDateTime,
} from "../../shared/local-date-time";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { DateTimeField } from "../../shared/forms";

import { ApiError } from "../../service-client";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  Toolbar,
  useCursor,
} from "../../shared/collection";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { Page } from "../../shared/page";
import { TraceTable } from "./list-table";
import type { ObservationSort } from "./sorting";
import { useListCosts } from "./list-cost";
import styles from "./traces.module.css";

function localTime(date: Date) {
  if (!Number.isFinite(date.getTime())) date = new Date();
  return formatLocalDateTime(date);
}
interface MetadataRow {
  id: string;
  key: string;
  value: string;
}
function metadataRow(entry?: string): MetadataRow {
  const index = entry?.indexOf("=") ?? -1;
  return {
    id: crypto.randomUUID(),
    key: index === -1 ? (entry ?? "") : (entry?.slice(0, index) ?? ""),
    value: index === -1 ? "" : (entry?.slice(index + 1) ?? ""),
  };
}
export function TracesPage() {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const descriptor = useQuery({
    queryKey: ["trace-query", workspace.id],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/trace-query", {
          params: { path: { workspace: workspace.id } },
          signal,
        })
        .then(data),
  });
  if (descriptor.isPending) return <Loading variant="detail" page />;
  if (descriptor.error)
    return (
      <ErrorNotice
        error={descriptor.error}
        retry={() => void descriptor.refetch()}
      />
    );
  if (!descriptor.data?.enabled)
    return (
      <Page title={t("Traces")}>
        <Empty
          title={t("Trace query disabled")}
          description={t(
            "Configure a trace query provider to inspect telemetry. Run execution is independent of trace query.",
          )}
        />
      </Page>
    );
  return (
    <TraceBrowser
      key={workspace.id + descriptor.data.provider}
      descriptor={descriptor.data}
    />
  );
}
function TraceBrowser({
  descriptor,
}: {
  descriptor: Schema["TraceQueryDescriptor"];
}) {
  const { t, i18n } = useTranslation(),
    [searchParams] = useSearchParams(),
    page = useCursor();
  const [from, setFrom] = useState(
      localTime(
        new Date(
          searchParams.get("from") ??
            Math.max(
              Date.now() - 86_400_000,
              Date.parse(descriptor.history_from ?? "") || 0,
            ),
        ),
      ),
    ),
    [to, setTo] = useState(
      localTime(new Date(searchParams.get("to") ?? Date.now())),
    ),
    [idQuery, setIdQuery] = useState(
      searchParams.get("session_id") ??
        searchParams.get("thread_id") ??
        searchParams.get("run_id") ??
        "",
    ),
    [metadataRows, setMetadataRows] = useState<MetadataRow[]>(() =>
      searchParams.getAll("metadata").map((entry) => metadataRow(entry)),
    );
  const appliedMetadata = useMemo(
    () =>
      metadataRows
        .map((row) => [row.key.trim(), row.value.trim()])
        .filter(([key, value]) => key !== "" && value !== "")
        .map(([key, value]) => `${key}=${value}`),
    [metadataRows],
  );
  const [filters, setFilters] = useState({
      from: new Date(from).toISOString(),
      to: new Date(to).toISOString(),
      ...idFilters(idQuery),
      metadata: appliedMetadata,
    }),
    [error, setError] = useState<Error>();
  useEffect(() => {
    const timer = setTimeout(() => {
      const start = new Date(from),
        end = new Date(to);
      if (
        !Number.isFinite(start.getTime()) ||
        !Number.isFinite(end.getTime()) ||
        end <= start ||
        (descriptor.history_from !== null &&
          start < new Date(descriptor.history_from)) ||
        end.getTime() - start.getTime() > 31 * 86_400_000
      ) {
        setError(new Error(t("Choose a valid time range of up to 31 days.")));
        return;
      }
      setError(undefined);
      page.reset();
      setFilters({
        from: start.toISOString(),
        to: end.toISOString(),
        ...idFilters(idQuery),
        metadata: appliedMetadata,
      });
    }, 350);
    return () => clearTimeout(timer);
  }, [from, to, idQuery, appliedMetadata]);
  const filtered = idQuery !== "" || appliedMetadata.length > 0;
  return (
    <Page
      title={t("Traces")}
      titleAction={
        <span
          className={styles.providerChip}
          title={
            descriptor.history_from
              ? `${t("Queryable since")} ${new Date(descriptor.history_from).toLocaleDateString(i18n.resolvedLanguage, { dateStyle: "medium" })}`
              : undefined
          }
        >
          {t("by")} <BrandIcon identity={descriptor.provider} size={13} />
          {descriptor.provider === "langfuse" ? "Langfuse" : "Logfire"}
        </span>
      }
      description={t(
        "Inspect attempt telemetry, model calls, and tool execution.",
      )}
      toolbar={
        <Toolbar
          search={idQuery}
          onSearchChange={setIdQuery}
          searchLabel={t("Search by ID")}
          searchPlaceholder={t("Session, thread, or run ID")}
          filters={
            <>
              <TimeRangeFilter
                from={from}
                to={to}
                historyFrom={descriptor.history_from}
                onChange={(nextFrom, nextTo) => {
                  setFrom(nextFrom);
                  setTo(nextTo);
                }}
              />
              <MetadataFilter rows={metadataRows} onChange={setMetadataRows} />
              {filtered && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setIdQuery("");
                    setMetadataRows([]);
                  }}
                >
                  {t("Clear")}
                </Button>
              )}
            </>
          }
        />
      }
    >
      <ErrorNotice error={error} />
      <TraceList filters={filters} page={page} />
    </Page>
  );
}
function idFilters(value: string) {
  const id = value.trim();
  return {
    session_id: /^(sess|session)_/.test(id) ? id : "",
    thread_id: id && !/^(sess|session|run)_/.test(id) ? id : "",
    run_id: id.startsWith("run_") ? id : "",
  };
}

function TimeRangeFilter({
  from,
  to,
  historyFrom,
  onChange,
}: {
  from: string;
  to: string;
  historyFrom?: string | null;
  onChange: (from: string, to: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const startDate = parseLocalDateTime(start),
    endDate = parseLocalDateTime(end);
  const invalid =
    !startDate ||
    !endDate ||
    startDate >= endDate ||
    endDate.getTime() - startDate.getTime() > 31 * 86_400_000 ||
    (!!historyFrom && !!startDate && startDate < new Date(historyFrom));
  const fmt = new Intl.DateTimeFormat(i18n.resolvedLanguage, {
    month: "short",
    day: "numeric",
  });
  const committedStart = parseLocalDateTime(from),
    committedEnd = parseLocalDateTime(to);
  const preset =
    committedStart &&
    committedEnd &&
    ([1, 7, 30] as const).find(
      (days) =>
        Math.abs(
          committedEnd.getTime() - committedStart.getTime() - days * 86_400_000,
        ) < 60_000,
    );
  const draftPreset =
    startDate &&
    endDate &&
    ([1, 7, 30] as const).find(
      (days) =>
        Math.abs(endDate.getTime() - startDate.getTime() - days * 86_400_000) <
        60_000,
    );
  const presetLabel = (days: number) =>
    t(
      days === 1
        ? "Last 24 hours"
        : days === 7
          ? "Last 7 days"
          : "Last 30 days",
    );
  return (
    <Popover
      open={open}
      onOpenChange={(value) => {
        if (value) {
          setStart(from);
          setEnd(to);
        }
        setOpen(value);
      }}
    >
      <PopoverTrigger
        render={<Button variant="outline" size="sm" type="button" />}
      >
        <span className={styles.chipLabel}>{t("Time range")}</span>
        {committedStart && committedEnd && (
          <span>
            {preset
              ? presetLabel(preset)
              : `${fmt.format(committedStart)} – ${fmt.format(committedEnd)}`}
          </span>
        )}
        <CaretDownIcon aria-hidden="true" />
      </PopoverTrigger>
      <PopoverPopup
        aria-label={t("Time range")}
        align="start"
        className={styles.filterPopup}
      >
        <div className={styles.filterPopover}>
          <div>
            <p className={styles.popoverTitle}>{t("Time range")}</p>
            <p className={styles.popoverHint}>
              {t("Traces are queried over the range you apply.")}
            </p>
          </div>
          <div className={styles.presets}>
            {[1, 7, 30].map((days) => (
              <Button
                key={days}
                type="button"
                variant="ghost"
                size="sm"
                aria-label={presetLabel(days)}
                aria-pressed={draftPreset === days}
                data-active={draftPreset === days || undefined}
                onClick={() => {
                  const now = new Date();
                  setStart(
                    formatLocalDateTime(
                      new Date(now.getTime() - days * 86400000),
                    ),
                  );
                  setEnd(formatLocalDateTime(now));
                }}
              >
                {t(days === 1 ? "24 hours" : days === 7 ? "7 days" : "30 days")}
              </Button>
            ))}
          </div>
          <div className={styles.rangeFields}>
            <DateTimeField
              label={t("Start time")}
              value={start}
              onValueChange={setStart}
            />
            <DateTimeField
              label={t("End time")}
              value={end}
              onValueChange={setEnd}
            />
          </div>
          {invalid && (
            <p role="alert" className={styles.popoverError}>
              {t("Choose a valid time range of up to 31 days.")}
            </p>
          )}
          <div className={styles.popoverFooter}>
            <span>{Intl.DateTimeFormat().resolvedOptions().timeZone}</span>
            <Button
              size="sm"
              type="button"
              disabled={invalid}
              onClick={() => {
                if (!startDate || !endDate) return;
                onChange(
                  formatLocalDateTime(startDate),
                  formatLocalDateTime(endDate),
                );
                setOpen(false);
              }}
            >
              {t("Apply")}
            </Button>
          </div>
        </div>
      </PopoverPopup>
    </Popover>
  );
}

function MetadataFilter({
  rows,
  onChange,
}: {
  rows: MetadataRow[];
  onChange: (rows: MetadataRow[]) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const active = rows.filter(
    (row) => row.key.trim() !== "" && row.value.trim() !== "",
  ).length;
  function update(id: string, patch: Partial<MetadataRow>) {
    onChange(rows.map((row) => (row.id === id ? { ...row, ...patch } : row)));
  }
  return (
    <Popover
      open={open}
      onOpenChange={(value) => {
        if (value && rows.length === 0) onChange([metadataRow()]);
        setOpen(value);
      }}
    >
      <PopoverTrigger
        render={<Button variant="outline" size="sm" type="button" />}
      >
        <span className={styles.chipLabel}>{t("Metadata")}</span>
        {active > 0 && <span>{active}</span>}
        <CaretDownIcon aria-hidden="true" />
      </PopoverTrigger>
      <PopoverPopup
        aria-label={t("Metadata")}
        align="start"
        className={`${styles.filterPopup} ${styles.metadataPopup}`}
      >
        <div className={styles.filterPopover}>
          <div>
            <p className={styles.popoverTitle}>{t("Metadata")}</p>
            <p className={styles.popoverHint}>
              {t("Exact key=value matches on run metadata.")}
            </p>
          </div>
          <div className={styles.metadataRows}>
            {rows.map((row, index) => (
              <div key={row.id} className={styles.metadataRow}>
                <Input
                  size="sm"
                  aria-label={t("Metadata key") + ` ${index + 1}`}
                  autoComplete="off"
                  placeholder={t("Key")}
                  value={row.key}
                  onChange={(event) =>
                    update(row.id, { key: event.target.value })
                  }
                />
                <Input
                  size="sm"
                  aria-label={t("Metadata value") + ` ${index + 1}`}
                  autoComplete="off"
                  placeholder={t("Value")}
                  value={row.value}
                  onChange={(event) =>
                    update(row.id, { value: event.target.value })
                  }
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label={t("Remove filter") + ` ${index + 1}`}
                  title={t("Remove filter") + ` ${index + 1}`}
                  onClick={() =>
                    onChange(rows.filter((item) => item.id !== row.id))
                  }
                >
                  <XIcon size={14} aria-hidden="true" />
                </Button>
              </div>
            ))}
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className={styles.addFilter}
            disabled={rows.length >= 8}
            onClick={() => onChange([...rows, metadataRow()])}
          >
            <PlusIcon aria-hidden="true" />
            {t("Add filter")}
          </Button>
        </div>
      </PopoverPopup>
    </Popover>
  );
}
interface TraceListProps {
  filters: {
    from?: string;
    to?: string;
    session_id?: string;
    thread_id?: string;
    run_id?: string;
    metadata?: string[];
  };
  page: ReturnType<typeof useCursor>;
}
export function TraceList({ filters, page }: TraceListProps) {
  const [sort, setSort] = useState<ObservationSort>({
    field: "started",
    direction: "desc",
  });
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation();
  const query = useQuery({
    queryKey: ["trace-list", workspace.id, filters, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/traces", {
          params: {
            path: { workspace: workspace.id },
            query: {
              ...filters,
              session_id: filters.session_id || undefined,
              thread_id: filters.thread_id || undefined,
              run_id: filters.run_id || undefined,
              metadata: filters.metadata?.length ? filters.metadata : undefined,
              cursor: page.cursor,
              view: "compact",
              limit: 25,
            },
          },
          signal,
        })
        .then(data),
  });
  const costs = useListCosts(
    workspace.id,
    query.isSuccess ? query.data.items : [],
  );
  if (query.isPending) return <Loading variant="table" columns={6} />;
  if (query.error instanceof ApiError && query.error.status === 503)
    return (
      <Empty
        title={t("Trace query unavailable")}
        description={t(
          "The trace backend is not configured or is temporarily unavailable. Run execution is independent of trace query.",
        )}
        action={
          <Button
            variant="outline"
            onClick={() => void query.refetch()}
            type="button"
          >
            {t("Try again")}
          </Button>
        }
      />
    );
  if (query.error || costs.error)
    return (
      <ErrorNotice
        error={query.error ?? costs.error}
        retry={() => {
          void query.refetch();
          void costs.refetch();
        }}
      />
    );
  const items = query.data?.items ?? [];
  return (
    <>
      {items.length ? (
        <TraceTable
          items={items}
          basePath={basePath}
          costs={costs.isSuccess ? costs.data : {}}
          sort={sort}
          onSortChange={setSort}
        />
      ) : (
        <Empty
          title={t("No traces in this range")}
          description={t(
            "Try a different range or fewer filters. Traces may be absent due to sampling, export, or retention.",
          )}
        />
      )}
      <CollectionFooter
        count={
          items.length
            ? t("{{count}} traces on this page", { count: items.length })
            : undefined
        }
      >
        <Pagination page={page} next={query.data?.next_cursor} />
      </CollectionFooter>
    </>
  );
}
