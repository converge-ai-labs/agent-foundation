import { useQuery } from "@tanstack/react-query";
import { ChartBarIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import {
  Empty,
  Pagination,
  ResourceTable,
  useCursor,
  type ResourceColumn,
} from "../../shared/collection";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { formatCost } from "../../shared/cost";
import { ModelKeyIcon } from "../models/model-icon";
import { duration, percent } from "./values";
import styles from "./usage.module.css";

type UsageRow = Schema["AgentUsage"] | Schema["ModelUsageGroup"];
export function Breakdown({
  window,
  group,
}: {
  window: { start: string; end: string };
  group: "agents" | "models";
}) {
  const { t, i18n } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace();
  const page = useCursor({ ...window, group });
  const query = useQuery({
    queryKey: ["usage", workspace.id, group, window, page.cursor],
    queryFn: async ({ signal }) =>
      data(
        await client
          .workspace(workspace.id)
          .GET(
            group === "agents"
              ? "/api/v1/usage/agents"
              : "/api/v1/usage/models",
            {
              params: { query: { ...window, cursor: page.cursor, limit: 50 } },
              signal,
            },
          ),
      ),
  });
  if (query.isPending) return <Loading variant="table" />;
  if (query.error)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  if (!query.data.items.length)
    return (
      <Empty
        icon={<ChartBarIcon aria-hidden="true" />}
        title={t("No usage in this period")}
        description={t(
          "Recorded model usage will appear here after agents run.",
        )}
      />
    );
  const number = (value: number) => value.toLocaleString(i18n.resolvedLanguage);
  const items = query.data.items.map((item) => ({
    ...item,
    key: "agent_id" in item ? item.agent_id : (item.model ?? "unattributed"),
  }));
  const columns: ResourceColumn<UsageRow>[] = [
    {
      label: group === "agents" ? t("Agent") : t("Model"),
      tone: "primary",
      render: (item) =>
        "model" in item ? (
          <div className={styles.model}>
            <ModelKeyIcon modelKey={item.model} size={20} />
            <div className={styles.identity}>
              <strong title={item.name ?? undefined}>
                {item.name ?? t("Unattributed model")}
              </strong>
              {item.model && <span title={item.model}>{item.model}</span>}
            </div>
          </div>
        ) : (
          <div className={styles.identity}>
            <strong title={item.name}>{item.name}</strong>
          </div>
        ),
    },
    {
      label: t("Requests"),
      align: "right",
      dataColumn: "requests",
      render: (item) => number(item.usage.requests),
    },
    {
      label: t("Input tokens"),
      align: "right",
      dataColumn: "input",
      render: (item) => number(item.usage.input_tokens),
    },
    {
      label: t("Output tokens"),
      align: "right",
      dataColumn: "output",
      render: (item) => number(item.usage.output_tokens),
    },
    {
      label: t("Cached input tokens"),
      align: "right",
      dataColumn: "cached",
      render: (item) => number(item.usage.cache_read_tokens),
    },
    {
      label: t("Cache rate"),
      align: "right",
      dataColumn: "cacheRate",
      render: (item) => percent(item.usage.cache_hit_rate),
    },
    {
      label: t("Spend"),
      align: "right",
      // The partial marker hangs outside the figures so costs stay aligned.
      render: (item) => (
        <span
          className={styles.cost}
          title={
            item.usage.unpriced_requests
              ? t("{{count}} requests could not be priced.", {
                  count: item.usage.unpriced_requests,
                })
              : undefined
          }
        >
          {formatCost(item.usage.cost)}
          {item.usage.unpriced_requests > 0 && (
            <span className={styles.partial}>*</span>
          )}
        </span>
      ),
    },
  ];
  if (group === "agents")
    columns.push(
      {
        label: t("Runs"),
        align: "right",
        dataColumn: "runs",
        render: (item) => ("runs" in item ? number(item.runs.runs) : null),
      },
      {
        label: t("Avg. run time"),
        align: "right",
        dataColumn: "runTime",
        render: (item) =>
          "runs" in item
            ? duration(
                item.runs.average_duration_seconds,
                i18n.resolvedLanguage,
              )
            : null,
      },
    );
  return (
    <>
      <ResourceTable
        items={items}
        columns={columns}
        caption={t("Usage breakdown")}
      />
      <Pagination page={page} next={query.data.next_cursor} />
    </>
  );
}
