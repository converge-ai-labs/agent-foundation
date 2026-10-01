import { useQuery } from "@tanstack/react-query";
import { PlugIcon } from "@phosphor-icons/react";
import { BrandIcon, Button, ChoiceField } from "a13n-ui";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data, matchesSearch, matchingPage } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  Toolbar,
  useCursor,
} from "../../shared/collection";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { Page, PageActions } from "../../shared/page";
import { ConnectionDetails } from "./editor";
import { ManageProvidersLink } from "../providers/manage-link";
import { connectorApi } from "../connectors/api";
import { NewConnection } from "./new";
import { MCPConnectionIcon } from "./mcp-icon";
import { connectionState, type RemoteCleanup } from "./api";
import styles from "./connections.module.css";

/** A remote endpoint reads better as its host than as a full URL. */
function endpointHost(url: string) {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

const STATES = ["ready", "pending", "reauthorization_required", "disabled"];

export function ConnectionsPage() {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation();
  const [search, setSearch] = useSearchParams();
  const [cleanup, setCleanup] = useState<RemoteCleanup>();
  const [query, setQuery] = useState("");
  const [state, setState] = useState("all");
  const term = query.trim().toLocaleLowerCase();
  const filtered = !!term || state !== "all";
  const page = useCursor({ term, state });
  const connections = useQuery({
    queryKey: ["connections", workspace.id, "list", page.cursor, term, state],
    enabled: can("read"),
    queryFn: ({ signal }) =>
      matchingPage(
        (cursor, limit) =>
          client
            .workspace(workspace.id)
            .GET("/api/v1/connections", {
              params: { query: { cursor, limit } },
              signal,
            })
            .then(data),
        page.cursor,
        filtered
          ? (connection) =>
              matchesSearch(
                term,
                connection.name,
                "app" in connection.config
                  ? connection.config.app
                  : connection.config.url,
              ) &&
              (state === "all" || connectionState(connection) === state)
          : undefined,
      ),
  });
  const rows = connections.data?.items ?? [];
  const focused = search.get("connection");
  const providers = useQuery({
    queryKey: ["connector-providers", "workspace", workspace.id, "picker"],
    enabled: can("read"),
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        connectorApi(client, workspace.id).providers(signal, cursor),
      ),
  });
  const select = (id?: string) =>
    setSearch(
      (current) => {
        const next = new URLSearchParams(current);
        if (id) next.set("connection", id);
        else next.delete("connection");
        return next;
      },
      { replace: true },
    );
  const create = can("write") && <NewConnection onConnected={select} />;
  return (
    <Page
      title={t("Connections")}
      description={t("Connect accounts and remote tools for your agents.")}
      toolbar={
        <Toolbar
          search={query}
          onSearchChange={setQuery}
          searchLabel={t("Search connections")}
          filters={
            <ChoiceField
              label={t("Status")}
              variant="filter"
              value={state}
              onValueChange={setState}
              options={[
                { value: "all", label: t("All statuses") },
                ...STATES.map((value) => ({
                  value,
                  label: t(`state.${value}`),
                })),
              ]}
            />
          }
        />
      }
    >
      <PageActions secondary>
        <ManageProvidersLink category="connectors" />
      </PageActions>
      <PageActions>{create}</PageActions>
      {cleanup && (
        <div className={styles.cleanup} role="status">
          <div>
            <h3>{t("Connection revoked")}</h3>
            <p>
              {t(
                {
                  skipped:
                    "External cleanup was skipped. If your provider still holds the authorization, remove it there.",
                  revoked: "External authorization was removed.",
                  failed:
                    "External cleanup failed. Remove the authorization with your provider.",
                  unknown:
                    "External cleanup could not be confirmed; check with your provider.",
                }[cleanup],
              )}
            </p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setCleanup(undefined)}
          >
            {t("Dismiss")}
          </Button>
        </div>
      )}
      <ErrorNotice error={connections.error} />
      {connections.isLoading ? (
        <Loading variant="table" columns={4} />
      ) : rows.length ? (
        <>
          <ResourceTable
            items={rows}
            caption={t("Connections")}
            onRowActivate={(row) => select(row.id)}
            columns={[
              {
                label: t("Connection"),
                tone: "primary",
                render: (row) => (
                  <ResourceIdentity
                    name={row.name}
                    resourceId={row.id}
                    description={
                      "app" in row.config
                        ? row.config.app
                        : endpointHost(row.config.url)
                    }
                    icon={
                      "app" in row.config ? (
                        <BrandIcon alias={row.config.app} />
                      ) : (
                        <MCPConnectionIcon endpoint={row.config.url} />
                      )
                    }
                  />
                ),
              },
              {
                label: t("Source"),
                render: (row) =>
                  row.type === "mcp"
                    ? t("Remote MCP")
                    : (providers.data?.find(
                        (provider) => provider.id === row.connector_provider_id,
                      )?.name ?? t("Connected account")),
              },
              {
                label: t("Status"),
                render: (connection) => (
                  <StatePill state={connectionState(connection)} />
                ),
              },
              {
                label: t("Updated"),
                tone: "muted",
                render: (connection) => (
                  <Timestamp value={connection.updated_at} relative />
                ),
              },
            ]}
          />
          <CollectionFooter
            count={t("{{count}} connections on this page", {
              count: rows.length,
            })}
          >
            <Pagination page={page} next={connections.data?.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        !connections.error && (
          <Empty
            icon={<PlugIcon aria-hidden="true" />}
            title={
              filtered ? t("No matching connections") : t("No connections yet")
            }
            description={
              filtered
                ? t("Change or clear the search and filters.")
                : t(
                    "Choose a service to connect your first account or MCP server.",
                  )
            }
            action={!filtered && create}
          />
        )
      )}
      {focused && can("read") && (
        <ConnectionDetails
          key={focused}
          connectionId={focused}
          onClose={() => select()}
          onCleanup={setCleanup}
        />
      )}
    </Page>
  );
}
