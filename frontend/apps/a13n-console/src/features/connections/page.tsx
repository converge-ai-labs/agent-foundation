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
import { ConnectionDetails } from "./editor";
import { ManageProvidersLink } from "../providers/manage-link";
import { connectorApi } from "../connectors/api";
import { NewConnection } from "./new";
import { NewConnectionNext } from "./next/new-connection";
import { useNextPreview } from "../../shared/next-preview";
import { MCPConnectionIcon } from "./mcp-icon";

export function ConnectionsPage() {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation();
  const [search, setSearch] = useSearchParams();
  const next = useNextPreview();
  const finalFocus = useRef<HTMLElement | null>(null);
  const [cleanup, setCleanup] = useState<Schema["ConnectionCleanupReceipt"]>();
  const connections = useInfiniteQuery({
    queryKey: ["connections", workspace.id, "list"],
    enabled: can("connection.read"),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ signal, pageParam }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/connections", {
          params: {
            path: { workspace: workspace.id },
            query: { cursor: pageParam },
          },
          signal,
        })
        .then(data),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const rows = connections.data?.pages.flatMap((page) => page.items) ?? [];
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
          {can("connection.manage") &&
            (next ? (
              <NewConnectionNext onConnected={select} />
            ) : (
              <NewConnection onConnected={select} />
            ))}
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
      <ErrorNotice error={connections.error} />
      {connections.isLoading ? (
        <Loading variant="table" columns={4} />
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
                  name={row.name}
                  resourceId={row.id}
                  description={
                    row.source.kind === "connector"
                      ? row.source.connector_key
                      : row.source.endpoint_url
                  }
                  icon={
                    row.source.kind === "connector" ? (
                      <BrandIcon alias={row.source.connector_key} />
                    ) : (
                      <MCPConnectionIcon endpoint={row.source.endpoint_url} />
                    )
                  }
                />
              ),
            },
            {
              label: t("Source"),
              render: (row) =>
                row.source.kind === "mcp"
                  ? t("Remote MCP")
                  : (providers.data?.find(
                      (provider) =>
                        row.source.kind === "connector" &&
                        provider.id === row.source.provider_id,
                    )?.name ?? t("Connected account")),
            },
            {
              label: t("Status"),
              render: (connection) => <StateBadge state={connection.status} />,
            },
          ]}
        />
      ) : (
        !connections.error && (
          <Empty
            title={t("No connections yet")}
            description={t(
              "Choose a service to connect your first account or MCP server.",
            )}
          />
        )
      )}
      {connections.hasNextPage && (
        <Button
          variant="outline"
          loading={connections.isFetchingNextPage}
          onClick={() => void connections.fetchNextPage()}
        >
          {t("Load more")}
        </Button>
      )}
      {focused && can("connection.read") && (
        <ConnectionDetails
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
