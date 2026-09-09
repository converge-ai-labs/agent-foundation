import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Button, Input, SelectField } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { ApiError } from "@converge.ai/a13n";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import {
  Page,
  ErrorNotice,
  Empty,
  Loading,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { Table, Pagination, useCursor } from "../../shared/collection";
import traceStyles from "./traces.module.css";
function localTime(date: Date) {
  if (!Number.isFinite(date.getTime())) date = new Date();
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 16);
}
export function TracesPage() {
  const { t } = useTranslation(),
    [searchParams] = useSearchParams(),
    page = useCursor();
  const [from, setFrom] = useState(
      localTime(new Date(searchParams.get("from") ?? Date.now() - 86_400_000)),
    ),
    [to, setTo] = useState(
      localTime(new Date(searchParams.get("to") ?? Date.now())),
    ),
    [query, setQuery] = useState(""),
    [searchIn, setSearchIn] = useState<Schema["SearchIn"]>("input_output"),
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
            query,
            search_in: searchIn,
            thread_id: thread,
            run_id: run,
            run_attempt_id: attempt,
          });
        }}
      >
        <div className={traceStyles.primaryFilters}>
          <Input
            label={t("From")}
            type="datetime-local"
            value={from}
            onChange={(event) => setFrom(event.target.value)}
            required
          />
          <Input
            label={t("To")}
            type="datetime-local"
            value={to}
            onChange={(event) => setTo(event.target.value)}
            required
          />
          <Input
            label={t("Search content")}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            maxLength={512}
          />
          <Button type="submit">{t("Apply filters")}</Button>
        </div>
        <details
          className={traceStyles.moreFilters}
          open={Boolean(
            searchParams.get("thread_id") ||
            searchParams.get("run_id") ||
            searchParams.get("run_attempt_id"),
          )}
        >
          <summary>{t("More filters")}</summary>
          <div className={traceStyles.advancedFilters}>
            <SelectField
              label={t("Search in")}
              placeholder={t("Select content")}
              value={searchIn}
              onValueChange={(value) => {
                if (
                  value === "input" ||
                  value === "output" ||
                  value === "input_output"
                )
                  setSearchIn(value);
              }}
              options={[
                { value: "input_output", label: t("Input and output") },
                { value: "input", label: t("Input") },
                { value: "output", label: t("Output") },
              ]}
            />
            <Input
              label={t("Thread ID")}
              value={thread}
              onChange={(event) => setThread(event.target.value)}
            />
            <Input
              label={t("Run ID")}
              value={run}
              onChange={(event) => setRun(event.target.value)}
            />
            <Input
              label={t("Attempt ID")}
              value={attempt}
              onChange={(event) => setAttempt(event.target.value)}
            />
          </div>
        </details>
      </form>
      <ErrorNotice error={error} />
      <TraceList filters={filters} page={page} />
    </Page>
  );
}
export function TraceList({
  filters,
  page,
}: {
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
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const query = useQuery({
    queryKey: ["trace-list", workspace.id, filters, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace_id}/traces", {
          params: {
            path: { workspace_id: workspace.id },
            query: {
              ...filters,
              query: filters.query || undefined,
              search_in: filters.query ? filters.search_in : undefined,
              thread_id: filters.thread_id || undefined,
              run_id: filters.run_id || undefined,
              run_attempt_id: filters.run_attempt_id || undefined,
              cursor: page.cursor,
            },
          },
          signal,
        })
        .then(data),
  });
  if (query.isPending) return <Loading />;
  if (query.error instanceof ApiError && query.error.status === 503)
    return (
      <Empty
        title={t("Trace query unavailable")}
        description={t(
          "The trace backend is not configured or is temporarily unavailable. Run execution is independent of trace query.",
        )}
        action={
          <Button onClick={() => void query.refetch()}>{t("Try again")}</Button>
        }
      />
    );
  if (query.error)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  return query.data?.items.length ? (
    <>
      <Table
        items={query.data.items}
        columns={[
          {
            label: t("Trace"),
            render: (item) => (
              <Link
                to={`/workspaces/${workspace.id}/traces/${encodeURIComponent(item.id)}`}
              >
                <strong>{item.name}</strong>
                <small>{item.id}</small>
              </Link>
            ),
          },
          {
            label: t("Started"),
            render: (item) => <Timestamp value={item.started_at} />,
          },
          {
            label: t("Telemetry"),
            render: (item) => <StateBadge state={item.trace_status} />,
          },
          {
            label: t("Attempt outcome"),
            render: (item) => (
              <StateBadge state={item.run_attempt_outcome ?? "unavailable"} />
            ),
          },
          {
            label: t("Duration"),
            render: (item) =>
              item.duration_ms === null
                ? t("Unavailable")
                : `${item.duration_ms} ms`,
          },
          {
            label: t("Cost (USD)"),
            render: (item) => item.total_cost_usd ?? t("Unavailable"),
          },
        ]}
      />
      <Pagination page={page} next={query.data.next_cursor} />
    </>
  ) : (
    <Empty
      title={t("No traces in this range")}
      description={t(
        "Try a different range or fewer filters. Traces may be absent due to sampling, export, or retention.",
      )}
    />
  );
}
