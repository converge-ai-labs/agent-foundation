import { useState } from "react";
import { useNavigate } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Button, SearchInput, Select } from "a13n-ui";
import { Bot, Plus, Layers } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, workspaceHeaders } from "../../shared/api";
import {
  Empty,
  ErrorNotice,
  Loading,
  Page,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import {
  Pagination,
  Table,
  ResourceIdentity,
  useCursor,
} from "../../shared/collection";
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
      description={t("The agents your workspace runs on.")}
      actions={create}
    >
      <div className={shared.toolbar}>
        <SearchInput
          label={t("Search this page")}
          placeholder={t("Find an agent…")}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <Select
          size="sm"
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
        <Table
          items={visible}
          columns={[
            {
              label: t("Agent"),
              render: (agent) => (
                <ResourceIdentity
                  to={agent.id}
                  name={agent.name}
                  description={agent.description || undefined}
                  icon={<Bot size={18} strokeWidth={1.5} />}
                />
              ),
            },
            {
              label: t("Model"),
              render: (agent) => (
                <AgentModel revisionId={agent.current_revision_id} />
              ),
            },
            {
              label: t("Status"),
              render: (agent) => (
                <StateBadge
                  state={
                    agent.archived_at
                      ? "archived"
                      : agent.enabled
                        ? "enabled"
                        : "disabled"
                  }
                />
              ),
            },
            {
              label: t("Updated"),
              render: (agent) => (
                <Timestamp value={agent.updated_at} relative />
              ),
            },
          ]}
        />
      )}
      {query.data && (
        <footer className={shared.collectionFooter}>
          <p>{t("{{count}} agents on this page", { count: visible.length })}</p>
          <Pagination page={page} next={query.data.next_cursor} />
        </footer>
      )}
    </Page>
  );
}

function AgentModel({ revisionId }: { revisionId: string }) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const revision = useQuery({
    queryKey: ["agent-revision", workspace.id, revisionId],
    staleTime: Infinity,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/agent-revisions/{agent_revision_id}", {
          params: { path: { agent_revision_id: revisionId } },
          headers: workspaceHeaders(workspace.id),
          signal,
        })
        .then(data),
  });
  return (
    <span className={styles.modelName}>
      <Layers size={13} aria-hidden="true" />
      {revision.isPending
        ? "…"
        : (revision.data?.config.model.model_key ?? t("Unavailable"))}
    </span>
  );
}
