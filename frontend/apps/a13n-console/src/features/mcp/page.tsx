import { useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { TreeStructureIcon } from "@phosphor-icons/react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import {
  Pagination,
  ResourceIdentity,
  ResourceTable,
  useCursor,
} from "../../shared/collection";
import {
  Empty,
  ErrorNotice,
  Loading,
  Page,
  StateBadge,
} from "../../shared/feedback";
import { JsonView } from "../../shared/form";
import { MCPEditor } from "./editor";

export function MCPPage() {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    page = useCursor(),
    [cleanup, setCleanup] = useState<Schema["ConnectionCleanupReceipt"]>();
  const query = useQuery({
    queryKey: ["mcp-connections", workspace.id, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/mcp-connections", {
          params: {
            path: { workspace: workspace.id },
            query: { cursor: page.cursor },
          },
          signal,
        })
        .then(data),
  });
  return (
    <Page
      title={t("MCP connections")}
      description={t(
        "Authorize remote MCP servers and make their tools available to agents.",
      )}
      actions={
        can("mcp_connection.manage") && <MCPEditor onCleanup={setCleanup} />
      }
    >
      {cleanup && (
        <div role="status">
          <h3>{t("Cleanup result")}</h3>
          <JsonView value={cleanup} />
          <Button
            size="sm"
            variant="outline"
            onClick={() => setCleanup(undefined)}
            type="button"
          >
            {t("Dismiss")}
          </Button>
        </div>
      )}
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            items={query.data.items}
            columns={[
              {
                label: t("Connection"),
                render: (item) => (
                  <ResourceIdentity
                    name={item.name}
                    description={item.endpoint_url}
                    icon={<TreeStructureIcon size={17} />}
                  />
                ),
              },
              {
                label: t("Authentication"),
                render: (item) => t(`auth.${item.auth_mode}`),
              },
              {
                label: t("Status"),
                render: (item) => (
                  <>
                    <StateBadge state={item.status} />
                    {item.status_reason && (
                      <small>{t(`state.${item.status_reason}`)}</small>
                    )}
                  </>
                ),
              },
              {
                label: t("Actions"),
                align: "right",
                render: (item) => (
                  <MCPEditor connectionId={item.id} onCleanup={setCleanup} />
                ),
              },
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        !query.error && (
          <Empty
            title={t("No MCP connections")}
            description={t(
              "Connect a remote Streamable HTTP MCP endpoint to get started.",
            )}
          />
        )
      )}
    </Page>
  );
}
