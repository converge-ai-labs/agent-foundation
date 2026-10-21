import {
  BrandIcon,
  Button,
  ChoiceField,
  Input,
  Popover,
  PopoverPopup,
  PopoverTrigger,
} from "a13n-ui";
import {
  CaretDownIcon,
  PlusIcon,
  PulseIcon,
  XIcon,
} from "@phosphor-icons/react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
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
import {
  RefreshButton,
  TimeRangePicker,
  type RangeSelection,
} from "../../shared/time-range";
import { backendName, useTraceBackend, type TraceBackendType } from "./backend";
import { TraceTable } from "./list-table";
import type { ObservationSort } from "./sorting";
import { useListCosts } from "./list-cost";
import styles from "./list.module.css";

const DAY = 86_400;
const timePresets = [DAY, 7 * DAY, 30 * DAY];
const defaultRange: RangeSelection = { preset: DAY };
// The Service selects its own `a13n.` attributes by ID, never by metadata.
const reservedAttributes = "a13n.";

/** A range linked from elsewhere, or the last 24 hours. */
function initialRange(params: URLSearchParams): RangeSelection {
  const from = params.get("from");
  if (!from) return defaultRange;
  const start = new Date(from),
    end = new Date(params.get("to") ?? Date.now());
  return Number.isFinite(start.getTime()) && Number.isFinite(end.getTime())
    ? { start, end }
    : defaultRange;
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
/** Attributes a root on this page carries that a metadata filter may select. */
function selectableKeys(items: readonly Schema["Span"][]) {
  const keys = new Set<string>();
  for (const item of items)
    for (const [key, value] of Object.entries(item.attributes))
      if (
        !key.startsWith(reservedAttributes) &&
        ["string", "number", "boolean"].includes(typeof value)
      )
        keys.add(key);
  return [...keys].sort();
}

export function TracesPage() {
  const { workspace } = useWorkspace(),
    { t } = useTranslation();
  const descriptor = useTraceBackend();
  if (descriptor.isPending) return <Loading variant="detail" page />;
  if (descriptor.error)
    return (
      <ErrorNotice
        error={descriptor.error}
        retry={() => void descriptor.refetch()}
      />
    );
  if (descriptor.data.type === null)
    return (
      <Page title={t("Traces")}>
        <Empty
          icon={<PulseIcon aria-hidden="true" />}
          title={t("Trace query disabled")}
          description={t(
            "Configure a trace query provider to inspect telemetry. Run execution is independent of trace query.",
          )}
        />
      </Page>
    );
  return (
    <TraceBrowser
      key={workspace.id + descriptor.data.type}
      backend={descriptor.data.type}
      queryableSince={descriptor.data.queryable_since}
    />
  );
}
function TraceBrowser({
  backend,
  queryableSince,
}: {
  backend: TraceBackendType;
  /** The earliest start the backend still finds; null when unbounded. */
  queryableSince: string | null;
}) {
  const { t, i18n } = useTranslation(),
    { workspace } = useWorkspace(),
    [searchParams] = useSearchParams();
  const earliest = queryableSince ? new Date(queryableSince) : null;
  const [range, setRange] = useState(() => initialRange(searchParams)),
    [now, setNow] = useState(Date.now),
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
        .map(([key, value]) => `${key}:${value}`),
    [metadataRows],
  );
  // Typed filters apply once the reader pauses; a picked range applies at once.
  const [typed, setTyped] = useState({
    id: idQuery,
    metadata: appliedMetadata,
  });
  useEffect(() => {
    const timer = setTimeout(
      () => setTyped({ id: idQuery, metadata: appliedMetadata }),
      350,
    );
    return () => clearTimeout(timer);
  }, [idQuery, appliedMetadata]);
  // A preset window ends now and never starts before the backend's retention.
  const lastSeconds = (seconds: number, end = now) => ({
    start: new Date(Math.max(end - seconds * 1000, earliest?.getTime() ?? 0)),
    end: new Date(end),
  });
  const shown = "preset" in range ? lastSeconds(range.preset) : range;
  const filters = {
    from: shown.start.toISOString(),
    to: shown.end.toISOString(),
    ...idFilters(typed.id),
    metadata: typed.metadata,
  };
  const page = useCursor(filters);
  const list = useTraceList(filters, page.cursor);
  const knownKeys = useMemo(
    () => selectableKeys(list.data?.items ?? []),
    [list.data],
  );
  const filtered =
    idQuery !== "" ||
    appliedMetadata.length > 0 ||
    !("preset" in range && range.preset === DAY);
  return (
    <Page
      title={t("Traces")}
      titleAction={
        <span
          className={styles.providerChip}
          title={
            queryableSince
              ? `${t("Queryable since")} ${new Date(queryableSince).toLocaleDateString(i18n.resolvedLanguage, { dateStyle: "medium" })}`
              : undefined
          }
        >
          {t("by")} <BrandIcon identity={backend} size={13} />
          {backendName(backend)}
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
              <TimeRangePicker
                value={range}
                presets={timePresets}
                maxDays={31}
                earliest={earliest}
                resolvePreset={(seconds) => lastSeconds(seconds, Date.now())}
                onChange={(next) => {
                  setRange(next);
                  setNow(Date.now());
                }}
              />
              <MetadataFilter
                rows={metadataRows}
                knownKeys={knownKeys}
                onChange={setMetadataRows}
              />
              {filtered && (
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => {
                    setRange(defaultRange);
                    setNow(Date.now());
                    setIdQuery("");
                    setMetadataRows([]);
                  }}
                >
                  {t("Clear")}
                </Button>
              )}
            </>
          }
          trailing={
            <RefreshButton
              pending={list.isFetching}
              onRefresh={() =>
                "preset" in range ? setNow(Date.now()) : void list.refetch()
              }
            />
          }
        />
      }
    >
      <TraceList query={list} page={page} workspaceId={workspace.id} />
    </Page>
  );
}
/** Session and run IDs carry their kind prefix; any other ID searches threads. */
function idFilters(value: string) {
  const id = value.trim();
  return {
    session_id: id.startsWith("sess_") ? id : "",
    thread_id: id && !/^(sess|run)_/.test(id) ? id : "",
    run_id: id.startsWith("run_") ? id : "",
  };
}

function MetadataFilter({
  rows,
  knownKeys,
  onChange,
}: {
  rows: MetadataRow[];
  /** Keys seen on the listed traces; a row's own key always stays choosable. */
  knownKeys: readonly string[];
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
        render={
          <Button
            variant="outline"
            type="button"
            className={styles.filterTrigger}
          />
        }
      >
        <span className={styles.chipLabel}>{t("Metadata")}</span>
        {active > 0 && <span className={styles.filterCount}>{active}</span>}
        <CaretDownIcon size={16} aria-hidden="true" />
      </PopoverTrigger>
      <PopoverPopup
        aria-label={t("Metadata")}
        align="start"
        className={styles.metadataPopup}
      >
        <div className={styles.filterPopover}>
          <div className={styles.metadataRows}>
            {rows.map((row, index) => {
              const keys = [
                ...new Set([...knownKeys, row.key].filter(Boolean)),
              ];
              return (
                <div key={row.id} className={styles.metadataRow}>
                  <ChoiceField
                    hideLabel
                    label={`${t("Metadata key")} ${index + 1}`}
                    placeholder={t("Attribute")}
                    value={row.key || undefined}
                    onValueChange={(key) => update(row.id, { key })}
                    options={
                      keys.length
                        ? keys.map((key) => ({ value: key, label: key }))
                        : [
                            {
                              value: "",
                              label: t("No attributes on this page"),
                              disabled: true,
                            },
                          ]
                    }
                  />
                  <Input
                    aria-label={`${t("Metadata value")} ${index + 1}`}
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
                    size="icon"
                    aria-label={`${t("Remove filter")} ${index + 1}`}
                    title={t("Remove filter")}
                    onClick={() =>
                      onChange(rows.filter((item) => item.id !== row.id))
                    }
                  >
                    <XIcon size={14} aria-hidden="true" />
                  </Button>
                </div>
              );
            })}
          </div>
          <div className={styles.popoverFooter}>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className={styles.addFilter}
              disabled={rows.length >= 8}
              onClick={() => onChange([...rows, metadataRow()])}
            >
              <PlusIcon aria-hidden="true" />
              {t("Add filter")}
            </Button>
            <span>{t("Exact match on root span attributes")}</span>
          </div>
        </div>
      </PopoverPopup>
    </Popover>
  );
}

type TraceFilters = {
  from: string;
  to: string;
  session_id: string;
  thread_id: string;
  run_id: string;
  /** `key:value` selectors, each an exact root span attribute. */
  metadata: string[];
};
/** One page of trace roots; the last page stays shown while the next loads. */
function useTraceList(filters: TraceFilters, cursor: string | undefined) {
  const client = useClient(),
    { workspace } = useWorkspace();
  const read = ({ signal }: { signal: AbortSignal }) =>
    client
      .workspace(workspace.id)
      .GET("/api/v1/traces", {
        params: {
          query: {
            started_after: filters.from,
            started_before: filters.to,
            session_id: filters.session_id || undefined,
            thread_id: filters.thread_id || undefined,
            run_id: filters.run_id || undefined,
            attribute: filters.metadata.length ? filters.metadata : undefined,
            cursor,
            limit: 25,
          },
        },
        signal,
      })
      .then(data);
  return useQuery({
    queryKey: ["trace-list", workspace.id, filters, cursor],
    queryFn: read,
    placeholderData: keepPreviousData,
  });
}

function TraceList({
  query,
  page,
  workspaceId,
}: {
  query: ReturnType<typeof useTraceList>;
  page: ReturnType<typeof useCursor>;
  workspaceId: string;
}) {
  const [order, setOrder] = useState<ObservationSort["direction"]>("desc");
  const { basePath } = useWorkspace(),
    { t } = useTranslation();
  const costs = useListCosts(
    workspaceId,
    query.isSuccess ? query.data.items : [],
  );
  if (query.isPending) return <Loading variant="table" columns={6} />;
  if (query.error instanceof ApiError && query.error.status === 503)
    return (
      <Empty
        icon={<PulseIcon aria-hidden="true" />}
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
    <div
      className={styles.results}
      aria-busy={query.isPlaceholderData || undefined}
    >
      {items.length ? (
        <TraceTable
          items={items}
          basePath={basePath}
          costs={costs.isSuccess ? costs.data : undefined}
          order={order}
          onOrderChange={setOrder}
        />
      ) : (
        <Empty
          icon={<PulseIcon aria-hidden="true" />}
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
    </div>
  );
}
