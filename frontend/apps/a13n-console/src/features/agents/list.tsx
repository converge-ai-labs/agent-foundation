import { Button, MenuItem, MenuSeparator } from "a13n-ui";
import { useQueries, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";
import {
  ArchiveIcon,
  DownloadSimpleIcon,
  PlayIcon,
  RobotIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  commandHeaders,
  data,
  representation,
  workspaceHeaders,
  type Schema,
} from "../../shared/api";
import {
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
import { useIdempotency } from "../../shared/idempotency";
import { Page } from "../../shared/page";
import { ModelIcon } from "../models/model-icon";
import { AgentAvatar } from "./avatar";
import { ExportAgent } from "./export";
import { AgentCreationMenu } from "./import";
import styles from "./agents.module.css";

/** The service lists agents by cursor without a search parameter, so search
 *  reads a bounded number of pages and matches them in the browser. */
const SEARCH_PAGES = 5;
const PAGE_SIZE = 30;

type Agent = Schema["Agent"];

export function Agents() {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can } = useWorkspace(),
    page = useCursor();
  const [archived, setArchived] = useState(false),
    [search, setSearch] = useState("");
  const query = search.trim().toLocaleLowerCase();
  const searching = query.length > 0;
  const list = useQuery({
    queryKey: ["agents", workspace.id, archived, page.cursor],
    enabled: !searching,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/agents", {
          params: {
            path: { workspace: workspace.id },
            query: {
              limit: PAGE_SIZE,
              cursor: page.cursor,
              include_archived: archived,
            },
          },
          signal,
        })
        .then(data),
  });
  const everything = useQuery({
    queryKey: ["agents-search", workspace.id, archived],
    enabled: searching,
    queryFn: async ({ signal }) => {
      const items: Agent[] = [];
      let cursor: string | undefined;
      let complete = true;
      for (let read = 0; read < SEARCH_PAGES; read++) {
        const result = await client.http
          .GET("/api/v1/workspaces/{workspace}/agents", {
            params: {
              path: { workspace: workspace.id },
              query: { limit: 100, cursor, include_archived: archived },
            },
            signal,
          })
          .then(data);
        items.push(...result.items);
        cursor = result.next_cursor ?? undefined;
        if (!cursor) break;
        if (read === SEARCH_PAGES - 1) complete = false;
      }
      return { items, complete };
    },
  });
  const active = searching ? everything : list;
  const items = searching
    ? (everything.data?.items ?? []).filter((agent) =>
        `${agent.name} ${agent.key} ${agent.description ?? ""}`
          .toLocaleLowerCase()
          .includes(query),
      )
    : (list.data?.items ?? []);
  const create = can("agent.create") ? <AgentCreationMenu /> : undefined;
  const clear = () => {
    setSearch("");
    setArchived(false);
    page.reset();
  };
  return (
    <Page
      className={styles.listPage}
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
              <Button
                type="button"
                size="sm"
                variant="outline"
                className={styles.chip}
                data-active={archived ? "true" : undefined}
                aria-pressed={archived}
                onClick={() => {
                  setArchived(!archived);
                  page.reset();
                }}
              >
                <ArchiveIcon size={13} aria-hidden="true" />
                {t("Archived")}
              </Button>
              {(searching || archived) && (
                <Button type="button" size="sm" variant="ghost" onClick={clear}>
                  {t("Clear")}
                </Button>
              )}
            </>
          }
        />
      }
    >
      {active.isPending ? (
        <Loading variant="table" columns={4} />
      ) : active.error ? (
        <ErrorNotice error={active.error} retry={() => void active.refetch()} />
      ) : !items.length ? (
        <Empty
          icon={<RobotIcon aria-hidden="true" />}
          title={
            searching
              ? t("No matching agents")
              : t("Your next agent starts here")
          }
          description={
            searching
              ? t(
                  "No agent in this workspace matches that name, key, or description.",
                )
              : t("Choose a model, give it instructions, and put it to work.")
          }
          action={!searching && create}
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
          {!searching && list.data && (
            <Pagination page={page} next={list.data.next_cursor} />
          )}
        </CollectionFooter>
      )}
      {searching && everything.data?.complete === false && (
        <p className={styles.searchBound}>
          {t("Search covers the first {{count}} agents in this workspace.", {
            count: everything.data.items.length,
          })}
        </p>
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
        client.http
          .GET("/api/v1/agent-revisions/{agent_revision_id}", {
            params: {
              path: { agent_revision_id: agent.default_revision_id! },
            },
            headers: workspaceHeaders(workspace.id),
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
  const state = (agent: Agent) =>
    agent.archived_at ? "archived" : agent.enabled ? "enabled" : "disabled";
  return (
    <div className={`${styles.listTable} a13n-scrollbar`}>
      <ResourceTable
        items={items}
        caption={t("Agents")}
        onRowActivate={(agent) => navigate(agent.key)}
        rowMenuLabel={t("Agent actions")}
        rowMenu={(agent) => {
          const revision = revisions[items.indexOf(agent)]?.data;
          return (
            <>
              {can("agent.invoke") && (
                <MenuItem
                  disabled={!agent.enabled || !!agent.archived_at}
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
                  version={revision.version}
                  trigger={
                    <MenuItem closeOnClick={false}>
                      <DownloadSimpleIcon size={14} aria-hidden="true" />
                      {t("Export agent")}
                    </MenuItem>
                  }
                />
              )}
              {can("agent.lifecycle") && (
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
                to={agent.key}
                name={agent.name}
                description={agent.description || agent.key}
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
            render: (agent) => {
              const revision = revisions[items.indexOf(agent)];
              if (!agent.default_revision_id)
                return <span className={styles.modelName}>—</span>;
              if (revision?.isPending) return <InlineLoading width="6rem" />;
              const key = revision?.data?.config.model.model_key;
              return (
                <span className={styles.modelName}>
                  {key ? (
                    <ModelIcon upstream={key} size={16} />
                  ) : (
                    <span aria-hidden="true" />
                  )}
                  <span title={key ?? undefined}>
                    {key ?? t("Unavailable")}
                  </span>
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

/** The list has no ETag for a row, so the confirmation reads it before acting. */
function ArchiveAgent({ agent }: { agent: Agent }) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace(),
    idempotency = useIdempotency();
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
      action={async () => {
        const current = representation(
          await client.http.GET(
            "/api/v1/workspaces/{workspace}/agents/{agent}",
            {
              params: { path: { workspace: workspace.id, agent: agent.id } },
              headers: workspaceHeaders(workspace.id),
            },
          ),
        );
        if (!current.etag)
          throw new Error(
            t("Version information is unavailable. Reload this page."),
          );
        const action = archived ? "unarchive" : "archive";
        await client.http.POST(
          "/api/v1/workspaces/{workspace}/agents/{agent}/{action}",
          {
            params: {
              path: { workspace: workspace.id, agent: agent.id, action },
              header: {
                ...commandHeaders(
                  workspace.id,
                  idempotency.forBody({ action, etag: current.etag }),
                ),
                "If-Match": current.etag,
              },
            },
          },
        );
        idempotency.reset();
      }}
    />
  );
}
