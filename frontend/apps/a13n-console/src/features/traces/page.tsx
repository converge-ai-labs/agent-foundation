import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
} from "a13n-ui";
import { formatLocalDateTime } from "../../shared/local-date-time";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { DateTimeField } from "../../shared/date-time-field";

import { ApiError } from "@converge.ai/a13n";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { Pagination, useCursor } from "../../shared/collection";
import {
  Empty,
  ErrorNotice,
  Loading,
  Page,
  Timestamp,
} from "../../shared/feedback";
import { TraceTable } from "./list-table";
import type { ObservationSort } from "./sorting";
import { useListCosts } from "./list-cost";
import traceStyles from "./traces.module.css";
function localTime(date: Date) {
  if (!Number.isFinite(date.getTime())) date = new Date();
  return formatLocalDateTime(date);
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
  const { t } = useTranslation(),
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
    [query, setQuery] = useState(""),
    [searchIn, setSearchIn] = useState<Schema["SearchIn"] | undefined>(
      descriptor.search_in[0],
    ),
    [thread, setThread] = useState(searchParams.get("thread_id") ?? ""),
    [run, setRun] = useState(searchParams.get("run_id") ?? ""),
    [attempt, setAttempt] = useState(searchParams.get("run_attempt_id") ?? "");
  const [filters, setFilters] = useState({
      from: new Date(from).toISOString(),
      to: new Date(to).toISOString(),
      query: "",
      search_in: searchIn,
      thread_id: thread,
      run_id: run,
      run_attempt_id: attempt,
    }),
    [error, setError] = useState<Error>();
  return (
    <Page
      title={t("Traces")}
      description={t(
        "Inspect attempt telemetry, model calls, and tool execution.",
      )}
    >
      <p className={traceStyles.providerNote}>
        {descriptor.provider}
        {descriptor.history_from && (
          <>
            {" "}
            · {t("Queryable since")}{" "}
            <Timestamp value={descriptor.history_from} />
          </>
        )}
      </p>
      <form
        className={traceStyles.filterForm}
        onSubmit={(event) => {
          event.preventDefault();
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
            setError(
              new Error(t("Choose a valid time range of up to 31 days.")),
            );
            return;
          }
          setError(undefined);
          page.reset();
          setFilters({
            from: start.toISOString(),
            to: end.toISOString(),
            query: descriptor.search_in.length ? query : "",
            search_in: searchIn,
            thread_id: thread,
            run_id: run,
            run_attempt_id: attempt,
          });
        }}
      >
        <div className={traceStyles.primaryFilters}>
          <DateTimeField
            label={t("From")}
            value={from}
            onValueChange={setFrom}
            clearable={false}
          />
          <DateTimeField
            label={t("To")}
            value={to}
            onValueChange={setTo}
            clearable={false}
          />
          <FormField className="min-w-0 w-full" label={t("Search content")}>
            <Input
              disabled={!descriptor.search_in.length}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              maxLength={512}
            />
          </FormField>
          <Button type="submit" variant="outline">
            {t("Apply filters")}
          </Button>
        </div>
        <DisclosureSection
          defaultOpen={Boolean(
            searchParams.get("thread_id") ||
            searchParams.get("run_id") ||
            searchParams.get("run_attempt_id"),
          )}
          title={<>{t("More filters")}</>}
        >
          <div className={traceStyles.advancedFilters}>
            <ChoiceField
              placeholder={t("Select content")}
              value={searchIn ?? ""}
              disabled={!descriptor.search_in.length}
              className="min-w-0"
              onValueChange={(value) => {
                if (
                  value === "input" ||
                  value === "output" ||
                  value === "input_output"
                )
                  setSearchIn(value);
              }}
              label={t("Search in")}
              options={descriptor.search_in.map((value) => ({
                value,
                label:
                  value === "input_output"
                    ? t("Input and output")
                    : value === "input"
                      ? t("Input")
                      : t("Output"),
              }))}
            />
            <FormField className="min-w-0 w-full" label={t("Thread ID")}>
              <Input
                value={thread}
                onChange={(event) => setThread(event.target.value)}
              />
            </FormField>
            <FormField className="min-w-0 w-full" label={t("Run ID")}>
              <Input
                value={run}
                onChange={(event) => setRun(event.target.value)}
              />
            </FormField>
            <FormField className="min-w-0 w-full" label={t("Attempt ID")}>
              <Input
                value={attempt}
                onChange={(event) => setAttempt(event.target.value)}
              />
            </FormField>
          </div>
        </DisclosureSection>
      </form>
      <ErrorNotice error={error} />
      <TraceList filters={filters} page={page} />
    </Page>
  );
}
interface TraceListProps {
  filters: {
    from?: string;
    to?: string;
    query?: string;
    search_in?: Schema["SearchIn"];
    thread_id?: string;
    run_id?: string;
    run_attempt_id?: string;
  };
  page: ReturnType<typeof useCursor>;
}
export function TraceList({ filters, page }: TraceListProps) {
  const { t } = useTranslation();
  const [view, setView] = useState<Schema["TraceView"]>("full");
  return (
    <>
      <div className={traceStyles.listToolbar}>
        <h2>{t("Trace activity")}</h2>
        <ChoiceField
          label={t("Content")}
          value={view}
          onValueChange={(value) => {
            if (value === "full" || value === "compact") {
              page.reset();
              setView(value);
            }
          }}
          options={[
            { value: "full", label: t("With previews") },
            { value: "compact", label: t("Compact") },
          ]}
        />
      </div>
      <TraceResults filters={filters} page={page} view={view} />
    </>
  );
}
function TraceResults({
  filters,
  page,
  view,
}: TraceListProps & { view: Schema["TraceView"] }) {
  const [sort, setSort] = useState<ObservationSort>({
    field: "started",
    direction: "desc",
  });
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation();
  const query = useQuery({
    queryKey: ["trace-list", workspace.id, filters, page.cursor, view],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/traces", {
          params: {
            path: { workspace: workspace.id },
            query: {
              ...filters,
              query: filters.query || undefined,
              search_in: filters.query ? filters.search_in : undefined,
              thread_id: filters.thread_id || undefined,
              run_id: filters.run_id || undefined,
              run_attempt_id: filters.run_attempt_id || undefined,
              cursor: page.cursor,
              view,
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
  return (
    <>
      {query.data?.items.length ? (
        <TraceTable
          items={query.data.items}
          basePath={basePath}
          view={view}
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
      <Pagination page={page} next={query.data?.next_cursor} />
    </>
  );
}
