import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowClockwise, ArrowUpRight } from "@phosphor-icons/react";
import { Button, ChoiceField, SearchPicker, SegmentedControl } from "a13n-ui";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice, PageHeader, TextField } from "../shell/ui";
import {
  compactNumber,
  estimatedCost,
  pageSize,
  rangeError,
  shiftDay,
  usageFilters,
} from "./state";
import styles from "./usage.module.css";

const number = (value: number) => value.toLocaleString();
function Count({ value }: { value: number }) {
  return (
    <span title={number(value)} aria-label={number(value)}>
      {compactNumber(value)}
    </span>
  );
}
function Cost({ totals }: { totals: Schema<"ConsumptionTotals"> }) {
  const exact =
    totals.model_cost_usd === null
      ? "Unknown"
      : `$${totals.model_cost_usd}${totals.unknown_model_costs ? "+" : ""}`;
  return (
    <span title={exact} aria-label={exact}>
      {estimatedCost(totals)}
    </span>
  );
}
const options = (items: Schema<"UsageOption">[] = []) =>
  items.map((item) => ({
    value: item.key,
    label: item.label,
    keywords: [item.key],
  }));

export function UsagePage() {
  const [params, setParams] = useSearchParams();
  const { client } = useTransport();
  const filters = usageFilters(params);
  const invalid = rangeError(filters);
  const query = useQuery({
    queryKey: ["usage-analytics", filters],
    queryFn: ({ signal }) =>
      result(client.GET("/api/usage", { params: { query: filters }, signal })),
    enabled: !invalid,
    placeholderData: keepPreviousData,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: "always",
  });
  const data = query.data;
  function change(values: Record<string, string | undefined>, paging = false) {
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      if (!paging) next.delete("offset");
      for (const [key, value] of Object.entries(values)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      return next;
    });
  }
  function narrow(group: Schema<"UsageGroup">) {
    if (filters.group_by === "project")
      change({
        project_id: group.project_id ?? undefined,
        projectless: group.project_id ? undefined : "true",
        thread_id: undefined,
        group_by: "thread",
      });
    else if (filters.group_by === "thread")
      change({ thread_id: group.thread_id ?? undefined, group_by: "model" });
    else change({ model: group.model ?? undefined, group_by: "thread" });
  }
  const project = filters.project_id
    ? `project:${filters.project_id}`
    : filters.projectless
      ? "none"
      : "all";
  const projectOptions = [
    { value: "all", label: "All projects" },
    { value: "none", label: "No project" },
    ...(data?.projects ?? []).map((item) => ({
      value: `project:${item.key}`,
      label: item.label,
    })),
  ];
  if (!projectOptions.some((item) => item.value === project))
    projectOptions.push({
      value: project,
      label: filters.project_id ?? "No project",
    });
  const threadOptions = [
    { value: "all", label: "All threads" },
    ...options(data?.threads),
  ];
  if (
    filters.thread_id &&
    !threadOptions.some((item) => item.value === filters.thread_id)
  )
    threadOptions.push({ value: filters.thread_id, label: filters.thread_id });
  const modelOptions = [
    { value: "all", label: "All models" },
    ...options(data?.models),
  ];
  if (
    filters.model &&
    !modelOptions.some((item) => item.value === filters.model)
  )
    modelOptions.push({ value: filters.model, label: filters.model });
  const offset = filters.offset ?? 0;
  return (
    <div className={styles.page}>
      <PageHeader
        title="Usage"
        description="Where your tokens and model estimated cost go."
        actions={
          <Button
            variant="ghost"
            disabled={query.isFetching || !!invalid}
            onClick={() => void query.refetch()}
          >
            <ArrowClockwise />
            Refresh
          </Button>
        }
      />
      <div className={styles.filters}>
        <ChoiceField
          label="Period"
          value={params.get("range") ?? "30"}
          options={[
            { value: "7", label: "Last 7 days" },
            { value: "30", label: "Last 30 days" },
            { value: "90", label: "Last 90 days" },
            { value: "custom", label: "Custom dates" },
          ]}
          onValueChange={(value) =>
            change({
              range: value,
              start: value === "custom" ? filters.start : undefined,
              end: value === "custom" ? filters.end : undefined,
            })
          }
        />
        <div className={styles.picker}>
          <span>Project</span>
          <SearchPicker
            label="Project"
            placeholder="All projects"
            emptyMessage="No projects found"
            groups={[{ label: "Projects", options: projectOptions }]}
            value={project}
            onValueChange={(value) =>
              change({
                project_id: value.startsWith("project:")
                  ? value.slice(8)
                  : undefined,
                projectless: value === "none" ? "true" : undefined,
                thread_id: undefined,
              })
            }
          />
        </div>
        <div className={styles.picker}>
          <span>Thread</span>
          <SearchPicker
            label="Thread"
            placeholder="All threads"
            emptyMessage="No threads found"
            groups={[{ label: "Root threads", options: threadOptions }]}
            value={filters.thread_id ?? "all"}
            onValueChange={(value) =>
              change({ thread_id: value === "all" ? undefined : value })
            }
          />
        </div>
        <div className={styles.picker}>
          <span>Actual model</span>
          <SearchPicker
            label="Actual model"
            placeholder="All models"
            emptyMessage="No models in this period"
            groups={[{ label: "Recorded models", options: modelOptions }]}
            value={filters.model ?? "all"}
            onValueChange={(value) =>
              change({ model: value === "all" ? undefined : value })
            }
          />
        </div>
      </div>
      {params.get("range") === "custom" && (
        <div className={styles.dates}>
          <TextField
            label="From (UTC)"
            type="date"
            value={filters.start}
            onChange={(value) => change({ start: value })}
          />
          <TextField
            label="Through (UTC)"
            type="date"
            value={shiftDay(filters.end, -1)}
            onChange={(value) => change({ end: shiftDay(value, 1) })}
          />
        </div>
      )}
      {invalid && <p role="alert">{invalid}</p>}
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      <p className={styles.freshness} role="status">
        {query.isFetching
          ? data
            ? "Updating · showing previous results…"
            : "Loading recorded usage…"
          : query.isError && data
            ? "Update failed · showing previous results"
            : data
              ? `As of ${new Date(data.as_of).toLocaleString()} · UTC days`
              : ""}
      </p>
      {data && !invalid && (
        <div aria-busy={query.isFetching} className={styles.results}>
          <dl className={styles.summary}>
            <div>
              <dt>Model estimated cost</dt>
              <dd>
                <Cost totals={data.summary} />
              </dd>
              <small>
                USD ·{" "}
                {data.summary.unknown_model_costs
                  ? `${number(data.summary.unknown_model_costs)} unknown-cost requests`
                  : "Recorded prices"}
              </small>
            </div>
            <div>
              <dt>Total tokens</dt>
              <dd>
                <Count value={data.summary.total_tokens} />
              </dd>
              <small>Input + output</small>
            </div>
            <div>
              <dt>Input tokens</dt>
              <dd>
                <Count value={data.summary.input_tokens} />
              </dd>
              <small>Includes cache reads / writes</small>
            </div>
            <div>
              <dt>Output tokens</dt>
              <dd>
                <Count value={data.summary.output_tokens} />
              </dd>
            </div>
            <div>
              <dt>Model requests</dt>
              <dd>
                <Count value={data.summary.model_requests} />
              </dd>
              <small>Recorded contributions</small>
            </div>
          </dl>
          <DailyChart key={`${data.start}:${data.end}`} days={data.daily} />
          <section className={styles.breakdown} aria-label="Usage breakdown">
            <div className={styles.sectionHeader}>
              <h2>Breakdown</h2>
              <SegmentedControl
                label="Group by"
                value={filters.group_by ?? "project"}
                onValueChange={(value) => change({ group_by: value })}
                options={[
                  { value: "project", label: "Projects" },
                  { value: "thread", label: "Threads" },
                  { value: "model", label: "Models" },
                ]}
              />
              <ChoiceField
                label="Sort"
                variant="filter"
                value={filters.order_by ?? "tokens"}
                onValueChange={(value) => change({ order_by: value })}
                options={[
                  { value: "tokens", label: "Most tokens" },
                  { value: "cost", label: "Highest cost" },
                ]}
              />
            </div>
            <div className={styles.tableScroll}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th scope="col">
                      {filters.group_by === "model"
                        ? "Actual model"
                        : filters.group_by === "thread"
                          ? "Thread"
                          : "Project"}
                    </th>
                    {[
                      "Requests",
                      "Input",
                      "Output",
                      "Cache read",
                      "Cache write",
                      "Est. cost (USD)",
                      "Unknown cost",
                    ].map((label) => (
                      <th scope="col" key={label}>
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.groups.map((group) => (
                    <tr key={group.key}>
                      <th scope="row">
                        <div className={styles.rowName}>
                          <button
                            disabled={query.isPlaceholderData}
                            onClick={() => narrow(group)}
                            title={`Filter to ${group.label}`}
                          >
                            {group.label}
                          </button>
                          {group.thread_id && (
                            <Link
                              to={`/threads/${encodeURIComponent(group.thread_id)}`}
                              aria-label={`Open conversation: ${group.label}`}
                              title="Open conversation"
                            >
                              <ArrowUpRight size={16} />
                            </Link>
                          )}
                        </div>
                      </th>
                      <td>
                        <Count value={group.totals.model_requests} />
                      </td>
                      <td>
                        <Count value={group.totals.input_tokens} />
                      </td>
                      <td>
                        <Count value={group.totals.output_tokens} />
                      </td>
                      <td>
                        <Count value={group.totals.cache_read_tokens} />
                      </td>
                      <td>
                        <Count value={group.totals.cache_write_tokens} />
                      </td>
                      <td>
                        <Cost totals={group.totals} />
                      </td>
                      <td>
                        <Count value={group.totals.unknown_model_costs} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!data.group_count && (
              <p className={styles.empty}>
                No model usage recorded for these filters.
              </p>
            )}
            <footer className={styles.pagination}>
              <span>
                {data.group_count
                  ? `${Math.min(offset + 1, data.group_count)}–${Math.min(offset + pageSize, data.group_count)} of ${number(data.group_count)} groups`
                  : "0 groups"}
              </span>
              <div>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!offset || query.isPlaceholderData}
                  onClick={() =>
                    change(
                      { offset: String(Math.max(0, offset - pageSize)) },
                      true,
                    )
                  }
                >
                  Previous
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={
                    offset + pageSize >= data.group_count ||
                    query.isPlaceholderData
                  }
                  onClick={() =>
                    change({ offset: String(offset + pageSize) }, true)
                  }
                >
                  Next
                </Button>
              </div>
            </footer>
          </section>
          <p className={styles.note}>
            Includes retained archived, Memory and Live threads, subagents and
            auxiliary model calls. Projects use each root thread’s current
            assignment. Model estimates are not invoices or subscription spend;
            unknown costs are excluded from known subtotals (+). Cache counters
            are subsets. Days use each record’s latest response time, not
            streaming increments. Missing records do not establish zero lifetime
            usage.
          </p>
        </div>
      )}
    </div>
  );
}

function DailyChart({ days }: { days: Schema<"UsageDay">[] }) {
  const [metric, setMetric] = useState("tokens");
  const [selected, setSelected] = useState(
    Math.max(
      0,
      days.findLastIndex((day) => day.totals.model_requests > 0),
    ),
  );
  const value = (day: Schema<"UsageDay">) =>
    metric === "tokens"
      ? day.totals.total_tokens
      : Number(day.totals.model_cost_usd ?? 0);
  const maximum = Math.max(...days.map(value), 0);
  const description = (day: Schema<"UsageDay">, exact = false) =>
    !day.totals.model_requests
      ? "No records"
      : metric === "tokens"
        ? `${exact ? number(day.totals.total_tokens) : compactNumber(day.totals.total_tokens)} tokens`
        : `${estimatedCost(day.totals, !exact)}${day.totals.unknown_model_costs ? ` · ${number(day.totals.unknown_model_costs)} unknown-cost requests` : ""}`;
  const current = days[selected];
  return (
    <section className={styles.trend} aria-label="Daily usage">
      <div className={styles.sectionHeader}>
        <h2>Daily usage</h2>
        <SegmentedControl
          label="Chart metric"
          value={metric}
          onValueChange={setMetric}
          options={[
            { value: "tokens", label: "Tokens" },
            { value: "cost", label: "Cost" },
          ]}
        />
        <span
          className={styles.chartReadout}
          title={current ? description(current, true) : undefined}
        >
          {current ? `${current.day} · ${description(current)}` : "No records"}
        </span>
      </div>
      <div className={styles.chartScale}>
        {metric === "tokens"
          ? `${compactNumber(maximum)} tokens`
          : `$${maximum >= 1000 ? compactNumber(maximum) : maximum.toLocaleString(undefined, { maximumFractionDigits: 4 })} known`}
      </div>
      <div className={styles.chartScroll}>
        <div className={styles.bars} style={{ minWidth: days.length * 6 }}>
          {days.map((day, index) => (
            <button
              key={day.day}
              className={styles.barTarget}
              title={`${day.day} · ${description(day, true)}`}
              aria-label={`${day.day} · ${description(day, true)}`}
              onMouseEnter={() => setSelected(index)}
              onFocus={() => setSelected(index)}
              onClick={() => setSelected(index)}
              onKeyDown={(event) => {
                const move =
                  event.key === "ArrowRight"
                    ? 1
                    : event.key === "ArrowLeft"
                      ? -1
                      : 0;
                if (move) {
                  event.preventDefault();
                  const target =
                    event.currentTarget.parentElement?.children[index + move];
                  if (target instanceof HTMLElement) target.focus();
                }
              }}
            >
              <span
                className={`${styles.bar} ${metric === "cost" && day.totals.unknown_model_costs ? styles.unknown : ""}`}
                style={{
                  height: `${maximum ? (value(day) / maximum) * 100 : 0}%`,
                  minHeight: day.totals.model_requests ? 3 : 1,
                  opacity: index === selected ? 1 : 0.65,
                }}
              />
            </button>
          ))}
        </div>
      </div>
      <div className={styles.axis}>
        <span>{days[0]?.day}</span>
        <span>UTC</span>
        <span>{days.at(-1)?.day}</span>
      </div>
    </section>
  );
}
