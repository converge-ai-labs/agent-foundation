import { useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { ArrowRightIcon, ChartBarIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceTable,
  type ResourceColumn,
} from "../../shared/collection";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { formatCost } from "../../shared/cost";
import { ModelKeyIcon } from "../models/model-icon";
import type { usagePage } from "./navigation";
import { duration, percent } from "./values";
import styles from "./usage.module.css";

export type UsageGroup = "agents" | "models" | "members";
type UsageRow =
  | Schema["AgentUsage"]
  | Schema["ModelUsageGroup"]
  | Schema["MemberUsage"]
  | Schema["SessionUsage"];
export function Breakdown({
  window,
  group,
  principalId,
  onMemberSelect,
  agentId,
  onAgentSelect,
  page,
}: {
  window: { start: string; end: string };
  page: ReturnType<typeof usagePage>;
  principalId?: string;
  onMemberSelect?: (member: Schema["MemberUsage"]) => void;
  onAgentSelect?: (agent: Schema["AgentUsage"]) => void;
} & (
  | { group: "sessions"; agentId: string }
  | { group: UsageGroup; agentId?: never }
)) {
  const { t, i18n } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace(),
    navigate = useNavigate();
  const sessionPath = (id: string) =>
    `/workspace/${workspace.id}/sessions/${encodeURIComponent(id)}?view=debug`;
  const query = useQuery({
    queryKey: [
      "usage",
      workspace.id,
      group,
      window,
      principalId,
      page.cursor,
      agentId,
    ],
    queryFn: async ({ signal }) =>
      data(
        await client
          .workspace(workspace.id)
          .GET(
            group === "sessions"
              ? "/api/v1/usage/sessions"
              : group === "members"
                ? "/api/v1/usage/members"
                : group === "agents"
                  ? "/api/v1/usage/agents"
                  : "/api/v1/usage/models",
            {
              params: {
                query: {
                  ...window,
                  principal_id: principalId,
                  ...(group === "sessions" ? { agent_id: agentId } : {}),
                  cursor: page.cursor,
                  limit: 50,
                },
              },
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
    key:
      "session_id" in item
        ? item.session_id
        : "principal_id" in item
          ? item.principal_id
          : "agent_id" in item
            ? item.agent_id
            : (item.model ?? "unattributed"),
  }));
  const columns: ResourceColumn<UsageRow>[] = [
    {
      label:
        group === "sessions"
          ? t("Session")
          : group === "members"
            ? t("Member")
            : group === "agents"
              ? t("Agent")
              : t("Model"),
      tone: "primary",
      render: (item) =>
        "session_id" in item ? (
          <div className={styles.identity}>
            <Link
              className={styles.identityLink}
              to={sessionPath(item.session_id)}
            >
              {item.title || t("Untitled session")}
            </Link>
            <span title={item.session_id}>{item.session_id}</span>
          </div>
        ) : "model" in item ? (
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
            {"agent_id" in item ? (
              <button
                className={styles.identityLink}
                type="button"
                onClick={() => onAgentSelect?.(item)}
                title={item.name}
              >
                {item.name}
              </button>
            ) : (
              <strong title={item.name}>{item.name}</strong>
            )}
            {"principal_id" in item && (
              <span>
                {t(item.kind === "user" ? "User" : "Service account")}
                {item.status === "disabled" ? ` · ${t("Disabled")}` : ""}
              </span>
            )}
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
      ariaSort: "descending",
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
  if (group !== "models")
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
  if (group === "members")
    columns.push({
      label: t("Actions"),
      align: "right",
      render: (item) =>
        "principal_id" in item ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onMemberSelect?.(item)}
          >
            {t("View usage")}
            <ArrowRightIcon aria-hidden="true" size={14} />
          </Button>
        ) : null,
    });
  return (
    <>
      <ResourceTable
        items={items}
        columns={columns}
        caption={t(group === "sessions" ? "Session usage" : "Usage breakdown")}
        onRowActivate={
          group !== "models"
            ? (item) => {
                if ("principal_id" in item) onMemberSelect?.(item);
                else if ("agent_id" in item) onAgentSelect?.(item);
                else if ("session_id" in item)
                  void navigate(sessionPath(item.session_id));
              }
            : undefined
        }
      />
      {(page.previous || query.data.next_cursor) && (
        <CollectionFooter>
          <Pagination page={page} next={query.data.next_cursor} />
        </CollectionFooter>
      )}
    </>
  );
}
