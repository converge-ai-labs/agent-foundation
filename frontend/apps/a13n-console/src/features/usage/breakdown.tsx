import { useQuery } from "@tanstack/react-query";
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
      render: (item) => (
        <div className={styles.identity}>
          <strong>{item.name ?? t("Unattributed model")}</strong>
          {"model" in item && item.model && <span>{item.model}</span>}
        </div>
      ),
    },
    {
      label: t("Requests"),
      align: "right",
      render: (item) => number(item.usage.requests),
    },
    {
      label: t("Input tokens"),
      align: "right",
      render: (item) => number(item.usage.input_tokens),
    },
    {
      label: t("Output tokens"),
      align: "right",
      render: (item) => number(item.usage.output_tokens),
    },
    {
      label: t("Cached input tokens"),
      align: "right",
      render: (item) => number(item.usage.cache_read_tokens),
    },
    {
      label: t("Cache rate"),
      align: "right",
      render: (item) => percent(item.usage.cache_hit_rate),
    },
    {
      label: t("Spend"),
      align: "right",
      render: (item) => (
        <span
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
            <span className={styles.partial}> *</span>
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
        render: (item) => ("runs" in item ? number(item.runs.runs) : null),
      },
      {
        label: t("Avg. run time"),
        align: "right",
        render: (item) =>
          "runs" in item ? duration(item.runs.average_duration_seconds) : null,
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
