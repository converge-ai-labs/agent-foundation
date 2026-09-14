import { Button, ChoiceField, FormField, Input } from "a13n-ui";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";

import { StackIcon, PlusIcon } from "@phosphor-icons/react";
import { AgentAvatar } from "./avatar";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, workspaceHeaders } from "../../shared/api";
import {
  Pagination,
  ResourceIdentity,
  ResourceTable,
  useCursor,
} from "../../shared/collection";
import {
  Empty,
  ErrorNotice,
  InlineLoading,
  Loading,
  Page,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import shared from "../../shared/shared.module.css";
import styles from "./agents.module.css";

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
        .GET("/api/v1/workspaces/{workspace}/agents", {
          params: {
            path: { workspace: workspace.id },
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
    <Button variant="default" onClick={() => navigate("new")} type="button">
      {<PlusIcon size={15} />}
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
      className={styles.listPage}
      title={t("Agents")}
      description={t("The agents your workspace runs on.")}
      actions={create}
    >
      <div className={shared.filters}>
        <FormField
          className="min-w-0 w-full"
          label={t("Search this page")}
          hideLabel={true}
        >
          <Input
            placeholder={t("Find an agent…")}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            type="search"
          />
        </FormField>
        <ChoiceField
          placeholder={t("Status")}
          value={filter}
          onValueChange={(value) => {
            setFilter(value);
            page.reset();
          }}
          label={t("Archive filter")}
          variant="filter"
          options={[
            { value: "active", label: t("Not archived") },
            { value: "all", label: t("Include archived") },
          ]}
        />
      </div>
      {query.isPending ? (
        <Loading variant="table" columns={4} />
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
        <div className={`${styles.listTable} a13n-scrollbar`}>
          <ResourceTable
            items={visible}
            caption={t("Agents")}
            onRowActivate={(agent) => navigate(agent.key)}
            columns={[
              {
                label: t("Agent"),
                tone: "primary",
                render: (agent) => (
                  <ResourceIdentity
                    to={agent.key}
                    name={agent.name}
                    description={agent.description || undefined}
                    resourceId={agent.id}
                    resourceKey={agent.key}
                    icon={
                      <AgentAvatar
                        name={agent.name}
                        id={agent.id}
                        url={agent.image_url}
                        className="size-8 rounded-lg"
                      />
                    }
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
                tone: "muted",
                render: (agent) => (
                  <Timestamp value={agent.updated_at} relative />
                ),
              },
            ]}
          />
        </div>
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
      <StackIcon size={13} aria-hidden="true" />
      {revision.isPending ? (
        <InlineLoading width="5.5rem" />
      ) : (
        (revision.data?.config.model.model_key ?? t("Unavailable"))
      )}
    </span>
  );
}
