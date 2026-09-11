import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { BrandIcon, Button } from "a13n-ui";
import { useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data, type Schema } from "../../shared/api";
import { ResourceIdentity, ResourceTable } from "../../shared/collection";
import {
  Empty,
  ErrorNotice,
  Loading,
  Page,
  StateBadge,
} from "../../shared/feedback";
import { ConnectionDetails } from "../connectors/connections";
import { MCPEditor } from "../mcp/editor";
import { ManageProvidersLink } from "../providers/manage-link";
import { connectorApi } from "../connectors/api";
import { NewConnection } from "./new";

type Row = { id: string } & (
  | { kind: "connector"; connection: Schema["ConnectorConnection"] }
  | { kind: "mcp"; connection: Schema["MCPConnection"] }
);
export function ConnectionsPage() {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation();
  const [search, setSearch] = useSearchParams();
  const finalFocus = useRef<HTMLElement | null>(null);
  const [cleanup, setCleanup] = useState<Schema["ConnectionCleanupReceipt"]>();
  const connectors = useInfiniteQuery({
    queryKey: ["connector-connections", workspace.id, "list"],
    enabled: can("connector_connection.read"),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ signal, pageParam }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/connector-connections", {
          params: {
            path: { workspace: workspace.id },
            query: { cursor: pageParam },
          },
          signal,
        })
        .then(data),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const mcp = useInfiniteQuery({
    queryKey: ["mcp-connections", workspace.id, "list"],
    enabled: can("mcp_connection.read"),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ signal, pageParam }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/mcp-connections", {
          params: {
            path: { workspace: workspace.id },
            query: { cursor: pageParam },
          },
          signal,
        })
        .then(data),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const rows: Row[] = [
    ...(connectors.data?.pages.flatMap((page) => page.items) ?? []).map(
      (connection) => ({
        id: connection.id,
        kind: "connector" as const,
        connection,
      }),
    ),
    ...(mcp.data?.pages.flatMap((page) => page.items) ?? []).map(
      (connection) => ({ id: connection.id, kind: "mcp" as const, connection }),
    ),
  ].sort(
    (a, b) =>
      b.connection.created_at.localeCompare(a.connection.created_at) ||
      a.id.localeCompare(b.id),
  );
  const focused = search.get("connection");
  const providers = useQuery({
    queryKey: ["connector-providers", "workspace", workspace.id, "picker"],
    enabled: can("connector_provider.read"),
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        connectorApi(client, { kind: "workspace", id: workspace.id }).providers(
          signal,
          cursor,
        ),
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
  return (
    <Page
      title={t("Connections")}
      description={t("Connect accounts and remote tools for your agents.")}
      actions={
        <>
          <ManageProvidersLink category="connectors" scope="workspace" />
          {(can("connector_connection.manage") ||
            can("mcp_connection.manage")) && (
            <NewConnection onConnected={select} />
          )}
        </>
      }
    >
      {cleanup && (
        <div role="status">
          <h3>
            {t(
              cleanup.local_status === "deleted"
                ? "Connection deleted"
                : "Connection revoked",
            )}
          </h3>
          <p>
            {t(
              {
                not_required:
                  "Local access is disabled. No external authorization needed cleanup.",
                succeeded:
                  "Local access is disabled and external authorization was removed.",
                failed:
                  "Local access is disabled, but external cleanup failed. Remove the authorization with your provider.",
                unknown:
                  "Local access is disabled. External cleanup could not be confirmed; check with your provider.",
              }[cleanup.remote_status],
            )}
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setCleanup(undefined)}
          >
            {t("Dismiss")}
          </Button>
        </div>
      )}
      <ErrorNotice error={connectors.error ?? mcp.error} />
      {connectors.isLoading || mcp.isLoading ? (
        <Loading />
      ) : rows.length ? (
        <ResourceTable
          items={rows}
          onRowActivate={(row, element) => {
            finalFocus.current = element;
            select(row.id);
          }}
          columns={[
            {
              label: t("Connection"),
              tone: "primary",
              render: (row) => (
                <ResourceIdentity
                  name={row.connection.name}
                  resourceId={row.connection.id}
                  description={
                    row.kind === "connector"
                      ? row.connection.connector_key
                      : row.connection.endpoint_url
                  }
                  icon={
                    row.kind === "connector" ? (
                      <BrandIcon alias={row.connection.connector_key} />
                    ) : (
                      <BrandIcon endpoint={row.connection.endpoint_url} />
                    )
                  }
                />
              ),
            },
            {
              label: t("Source"),
              render: (row) =>
                row.kind === "mcp"
                  ? t("Remote MCP")
                  : (providers.data?.find(
                      (provider) =>
                        provider.id === row.connection.connector_provider_id,
                    )?.name ?? t("Connected account")),
            },
            {
              label: t("Status"),
              render: ({ connection }) => (
                <StateBadge state={connection.status} />
              ),
            },
          ]}
        />
      ) : (
        !connectors.error &&
        !mcp.error && (
          <Empty
            title={t("No connections yet")}
            description={t(
              "Choose a service to connect your first account or MCP server.",
            )}
          />
        )
      )}
      {(connectors.hasNextPage || mcp.hasNextPage) && (
        <Button
          variant="outline"
          loading={connectors.isFetchingNextPage || mcp.isFetchingNextPage}
          onClick={() => {
            if (connectors.hasNextPage) void connectors.fetchNextPage();
            if (mcp.hasNextPage) void mcp.fetchNextPage();
          }}
        >
          {t("Load more")}
        </Button>
      )}
      {focused?.startsWith("cconn_") && can("connector_connection.read") && (
        <ConnectionDetails
          key={focused}
          connectionId={focused}
          finalFocus={finalFocus}
          controlledOpen
          onClose={() => select()}
          onCleanup={setCleanup}
        />
      )}
      {focused?.startsWith("mcpc_") && can("mcp_connection.read") && (
        <MCPEditor
          key={focused}
          connectionId={focused}
          finalFocus={finalFocus}
          controlledOpen
          onClose={() => select()}
          onCleanup={setCleanup}
        />
      )}
    </Page>
  );
}
