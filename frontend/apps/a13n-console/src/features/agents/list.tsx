import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Button, Input, Select, Badge } from "a13n-ui";
import { Bot, Plus, ArrowUpRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import {
  Empty,
  ErrorNotice,
  Loading,
  Page,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { Pagination, useCursor } from "../../shared/collection";
import styles from "./agents.module.css";
import shared from "../../shared/shared.module.css";

export function Agents() {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can } = useWorkspace(),
    page = useCursor(),
    navigate = useNavigate();
  const [filter, setFilter] = useState("active"),
    [search, setSearch] = useState("");
  const query = useQuery({
    queryKey: ["agents", workspace.id, filter, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace_id}/agents", {
          params: {
            path: { workspace_id: workspace.id },
            query: {
              limit: 30,
              cursor: page.cursor,
              include_archived: filter === "all",
            },
          },
          signal,
        })
        .then(data),
  });
  const create = can("agent.create") ? (
    <Button
      variant="primary"
      icon={<Plus size={15} />}
      onClick={() => navigate("new")}
    >
      {t("Create agent")}
    </Button>
  ) : undefined;
  const visible =
    query.data?.items.filter((item) =>
      `${item.name} ${item.description ?? ""}`
        .toLocaleLowerCase()
        .includes(search.toLocaleLowerCase()),
    ) ?? [];
  return (
    <Page
      title={t("Agents")}
      description={t("Build the agents that move your work forward.")}
      actions={create}
    >
      <div className={shared.toolbar}>
        <Input
          label={t("Search this page")}
          placeholder={t("Find an agent…")}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <Select
          label={t("Agent status")}
          placeholder={t("Status")}
          value={filter}
          onValueChange={(value) => {
            setFilter(value);
            page.reset();
          }}
          options={[
            { value: "active", label: t("Current agents") },
            { value: "all", label: t("Include archived") },
          ]}
        />
      </div>
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      ) : !visible.length ? (
        <Empty
          title={t(
            search ? "No matching agents" : "Your next agent starts here",
          )}
          description={t(
            search
              ? "Try a different search on this page."
              : "Choose a model, give it instructions, and put it to work.",
          )}
          action={!search && create}
        />
      ) : (
        <div className={styles.grid}>
          {visible.map((agent) => (
            <Link to={agent.id} key={agent.id} className={styles.card}>
              <div className={styles.cardTop}>
                <span className={styles.agentIcon}>
                  <Bot size={21} strokeWidth={1.5} />
                </span>
                <StateBadge
                  state={
                    agent.archived_at
                      ? "archived"
                      : agent.enabled
                        ? "enabled"
                        : "disabled"
                  }
                />
              </div>
              <h2>
                {agent.name}
                <ArrowUpRight size={14} />
              </h2>
              <p>{agent.description || t("No description")}</p>
              <footer>
                <Badge>v{agent.version}</Badge>
                <span>
                  <Timestamp value={agent.updated_at} />
                </span>
              </footer>
            </Link>
          ))}
        </div>
      )}
      {query.data && <Pagination page={page} next={query.data.next_cursor} />}
    </Page>
  );
}
