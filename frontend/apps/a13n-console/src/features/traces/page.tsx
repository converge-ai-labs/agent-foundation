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
import { Link, useSearchParams } from "react-router";
import { DateTimeField } from "../../shared/date-time-field";

import { ApiError } from "@converge.ai/a13n";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import {
  Empty,
  ErrorNotice,
  Loading,
  Page,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import traceStyles from "./traces.module.css";
function localTime(date: Date) {
  if (!Number.isFinite(date.getTime())) date = new Date();
  return formatLocalDateTime(date);
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
              value={searchIn}
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
              options={[
                { value: "input_output", label: t("Input and output") },
                { value: "input", label: t("Input") },
                { value: "output", label: t("Output") },
              ]}
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
  if (query.error)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  return query.data?.items.length ? (
    <>
      <ResourceTable
        items={query.data.items}
        columns={[
          {
            label: t("Trace"),
            tone: "primary",
            render: (item) => (
              <Link to={`${basePath}/traces/${encodeURIComponent(item.id)}`}>
                <strong>{item.name}</strong>
                <small>{item.id}</small>
              </Link>
            ),
          },
          {
            label: t("Started"),
            tone: "muted",
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
            align: "right",
            render: (item) =>
              item.duration_ms === null
                ? t("Unavailable")
                : `${item.duration_ms} ms`,
          },
          {
            label: t("Cost (USD)"),
            align: "right",
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
