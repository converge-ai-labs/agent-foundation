import { Button, MenuItem, MenuSeparator } from "a13n-ui";
import { useQueries, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";
import {
  ArchiveIcon,
  DownloadSimpleIcon,
  PlayIcon,
  HeartIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, rowTag, type Schema } from "../../shared/api";
import {
  ArchivedFilter,
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  Toolbar,
  useCursor,
} from "../../shared/collection";
import { Confirm } from "../../shared/dialogs";
import {
  ErrorNotice,
  InlineLoading,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { Page } from "../../shared/page";
import { ModelIcon } from "../models/model-icon";
import { AgentAvatar } from "./avatar";
import { ExportAgent } from "./export";
import { AgentCreationMenu } from "./import";
import { useModelsByKey } from "./queries";
import styles from "./agents.module.css";

const PAGE_SIZE = 30;

type Agent = Schema["Agent"];

export function Agents() {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can } = useWorkspace();
  const [archived, setArchived] = useState(false),
    [search, setSearch] = useState("");
  const query = search.trim().toLocaleLowerCase();
  const page = useCursor({ query, archived });
  const searching = query.length > 0;
  const filtered = searching || archived;
  const list = useQuery({
    queryKey: ["agents", workspace.id, query, archived, page.cursor],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/agents", {
          params: {
            query: {
              limit: PAGE_SIZE,
              cursor: page.cursor,
              // The chip adds archived items; omitting the filter lists both.
              ...(!archived && { archived: false }),
              ...(searching && { q: query }),
            },
          },
          signal,
        })
        .then(data),
  });
  const items = list.data?.items ?? [];
  const create = can("write") ? <AgentCreationMenu /> : undefined;
  const clear = () => {
    setSearch("");
    setArchived(false);
  };
  return (
    <Page
      title={t("Agents")}
      description={t("The agents your workspace runs on.")}
      actions={create}
      toolbar={
        <Toolbar
          search={search}
          onSearchChange={setSearch}
          searchLabel={t("Find an agent…")}
          filters={
            <>
              <ArchivedFilter value={archived} onChange={setArchived} />
              {filtered && (
                <Button type="button" size="sm" variant="ghost" onClick={clear}>
                  {t("Clear")}
                </Button>
              )}
            </>
          }
        />
      }
    >
      {list.isPending ? (
        <Loading variant="table" columns={4} />
      ) : list.error ? (
        <ErrorNotice error={list.error} retry={() => void list.refetch()} />
      ) : !items.length ? (
        <Empty
          icon={<HeartIcon aria-hidden="true" />}
          title={
            filtered
              ? t("No matching agents")
              : t("Your next agent starts here")
          }
          description={
            searching
              ? t(
                  "No agent in this workspace matches that name or description.",
                )
              : archived
                ? t("Change or clear the search and filters.")
                : t(
                    "Describe what you need to AI Composer, or configure your agent yourself.",
                  )
          }
          action={!filtered && create}
        />
      ) : (
        <AgentRows items={items} />
      )}
      {!!items.length && (
        <CollectionFooter
          count={
            searching
              ? t("{{count}} matching agents", { count: items.length })
              : t("{{count}} agents on this page", { count: items.length })
          }
        >
          <Pagination page={page} next={list.data?.next_cursor} />
        </CollectionFooter>
      )}
    </Page>
  );
}

/** One revision request per row on the page, batched and shared by cache key. */
function useRevisions(items: readonly Agent[]) {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQueries({
    queries: items.map((agent) => ({
      queryKey: ["agent-revision", workspace.id, agent.default_revision_id],
      enabled: !!agent.default_revision_id,
      staleTime: Infinity,
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        client
          .workspace(workspace.id)
          .GET("/api/v1/agents/{agent_id}/revisions/{revision_id}", {
            params: {
              path: {
                agent_id: agent.id,
                revision_id: agent.default_revision_id!,
              },
            },
            signal,
          })
          .then(data),
    })),
  });
}

function AgentRows({ items }: { items: readonly Agent[] }) {
  const { t } = useTranslation(),
    { can, basePath } = useWorkspace(),
    navigate = useNavigate();
  const revisions = useRevisions(items);
  const modelsById = useModelsByKey();
  const state = (agent: Agent) => (agent.archived_at ? "archived" : "enabled");
  return (
    <div className={styles.listTable}>
      <ResourceTable
        items={items}
        caption={t("Agents")}
        onRowActivate={(agent) => navigate(agent.id)}
        rowMenuLabel={t("Agent actions")}
        rowMenu={(agent) => {
          const revision = revisions[items.indexOf(agent)]?.data;
          return (
            <>
              {can("run") && (
                <MenuItem
                  disabled={!!agent.archived_at}
                  onClick={() =>
                    navigate(`${basePath}/sessions/new?agent=${agent.id}`)
                  }
                >
                  <PlayIcon size={14} aria-hidden="true" />
                  {t("Try agent")}
                </MenuItem>
              )}
              {revision && (
                <ExportAgent
                  agent={agent}
                  config={revision.config}
                  version={revision.number}
                  trigger={
                    <MenuItem closeOnClick={false}>
                      <DownloadSimpleIcon size={14} aria-hidden="true" />
                      {t("Export agent")}
                    </MenuItem>
                  }
                />
              )}
              {can("write") && agent.source === "custom" && (
                <>
                  <MenuSeparator />
                  <ArchiveAgent agent={agent} />
                </>
              )}
            </>
          );
        }}
        columns={[
          {
            label: t("Agent"),
            tone: "primary",
            render: (agent) => (
              <ResourceIdentity
                to={agent.id}
                name={agent.name}
                description={agent.description || agent.id}
                resourceId={agent.id}
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
            render: (agent) => {
              const revision = revisions[items.indexOf(agent)];
              if (!agent.default_revision_id)
                return <span className={styles.modelName}>—</span>;
              if (revision?.isPending) return <InlineLoading width="6rem" />;
              const id = revision?.data?.config.model;
              const model = id ? modelsById.get(id) : undefined;
              return (
                <span className={styles.modelName} title={id ?? undefined}>
                  {model ? (
                    <ModelIcon upstream={model.config.model_name} size={16} />
                  ) : (
                    <span aria-hidden="true" />
                  )}
                  <span>{model?.name ?? id ?? t("Unavailable")}</span>
                </span>
              );
            },
          },
          {
            label: t("Status"),
            render: (agent) => <StatePill state={state(agent)} />,
          },
          {
            label: t("Updated"),
            tone: "muted",
            render: (agent) => <Timestamp value={agent.updated_at} relative />,
          },
        ]}
      />
    </div>
  );
}

/** The row's ETag is the one the reader saw; a stale row fails its precondition. */
function ArchiveAgent({ agent }: { agent: Agent }) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace();
  const archived = !!agent.archived_at;
  return (
    <Confirm
      subject={agent.name}
      title={t(archived ? "Unarchive agent" : "Archive agent")}
      description={t(
        "Archived agents leave the default list. Their history remains available.",
      )}
      danger={!archived}
      triggerElement={
        <MenuItem
          closeOnClick={false}
          variant={archived ? "default" : "destructive"}
        >
          <ArchiveIcon size={14} aria-hidden="true" />
          {t(archived ? "Unarchive" : "Archive")}
        </MenuItem>
      }
      action={() =>
        client
          .workspace(workspace.id)
          .POST(
            archived
              ? "/api/v1/agents/{agent_id}/unarchive"
              : "/api/v1/agents/{agent_id}/archive",
            {
              params: {
                path: { agent_id: agent.id },
              },
              headers: ifMatch(rowTag(agent)),
            },
          )
          .then(data)
      }
    />
  );
}
