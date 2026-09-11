import { ResourceEditorButton } from "../../shared/resource-editor-button";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FormField,
  Input,
  ModalFrame,
  Tabs,
  TabsList,
  TabsPanel,
  TabsTab,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import { ResourceIdentity } from "../../shared/collection";
import { Confirm, FormActions, JsonView } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";
import { MCPAuthorization } from "./authorization";
import { CreateMCP } from "./create";
import { MCPTools } from "./tools";

export function MCPEditor({
  connectionId,
  onCleanup,
}: {
  connectionId?: string;
  onCleanup: (receipt: Schema["ConnectionCleanupReceipt"]) => void;
}) {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    [open, setOpen] = useState(false),
    [created, setCreated] = useState<string>(),
    [generation, setGeneration] = useState(0),
    id = connectionId ?? created;
  const query = useQuery({
    queryKey: ["mcp-connections", workspace.id, id],
    enabled: open && !!id,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/mcp-connections/{connection_id}", {
          params: { path: { connection_id: id! } },
          signal,
        })
        .then(data),
  });
  async function reload() {
    await query.refetch();
    setGeneration((value) => value + 1);
  }
  return (
    <ModalFrame
      onOpenChange={(value) => {
        setOpen(value);
        if (!value) setCreated(undefined);
      }}
      trigger={
        <ResourceEditorButton
          editing={!!connectionId}
          createLabel="Connect MCP server"
          editLabel="Details"
        />
      }
      size={"md"}
      title={t(id ? "MCP connection" : "Connect MCP server")}
      description={t(
        "Endpoint and authentication mode are fixed after creation. Credentials are never returned.",
      )}
      closeLabel={t("Close")}
      open={open}
    >
      {open &&
        (!id ? (
          <CreateMCP onSuccess={(value) => setCreated(value.id)} />
        ) : query.isPending ? (
          <Loading />
        ) : query.error ? (
          <ErrorNotice error={query.error} />
        ) : (
          query.data &&
          (can("mcp_connection.manage") ? (
            <Tabs
              key={generation}
              defaultValue={created ? "authorization" : "details"}
            >
              <TabsList aria-label={t("MCP connection")}>
                <TabsTab value={"details"}>{t("Details")}</TabsTab>
                <TabsTab value={"authorization"}>{t("Authorization")}</TabsTab>
                <TabsTab value={"tools"}>{t("Tools")}</TabsTab>
              </TabsList>
              <TabsPanel value={"details"}>
                {
                  <MCPSettings
                    onCleanup={onCleanup}
                    initial={query.data}
                    reload={reload}
                    close={() => setOpen(false)}
                  />
                }
              </TabsPanel>
              <TabsPanel value={"authorization"}>
                {<MCPAuthorization initial={query.data} reload={reload} />}
              </TabsPanel>
              <TabsPanel value={"tools"}>
                {<MCPTools connection={query.data} />}
              </TabsPanel>
            </Tabs>
          ) : (
            <JsonView value={query.data} />
          ))
        ))}
    </ModalFrame>
  );
}

export function MCPSettings({
  initial,
  reload,
  close,
  onCleanup,
}: {
  onCleanup: (receipt: Schema["ConnectionCleanupReceipt"]) => void;
  initial: Schema["MCPConnection"];
  reload: () => Promise<void>;
  close: () => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation(),
    key = useIdempotency(),
    [basis] = useState(initial),
    [name, setName] = useState(initial.name);
  function done() {
    void cache.invalidateQueries({ queryKey: ["mcp-connections"] });
    close();
  }
  const save = useMutation({
    mutationFn: () =>
      client.http
        .PATCH("/api/v1/mcp-connections/{connection_id}", {
          params: { path: { connection_id: basis.id } },
          body: { name, expected_version: basis.version },
        })
        .then(data),
    onSuccess: done,
  });
  return (
    <div className={styles.stack}>
      <ResourceIdentity name={basis.name} resourceId={basis.id} />
      <p className={styles.muted}>{basis.endpoint_url}</p>
      <StateBadge state={basis.status} />
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <FormField className="min-w-0 w-full" label={t("Name")}>
          <Input
            required={true}
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={128}
          />
        </FormField>
        <ErrorNotice error={save.error} retry={() => void reload()} />
        <FormActions pending={save.isPending} />
      </form>
      <div className={styles.actions}>
        <Confirm
          title={t(
            basis.status === "disabled"
              ? "Enable connection"
              : "Disable connection",
          )}
          description={t(
            "This changes whether new agent calls can use the connection.",
          )}
          trigger={t(basis.status === "disabled" ? "Enable" : "Disable")}
          action={async () => {
            const action = basis.status === "disabled" ? "enable" : "disable",
              body = { expected_version: basis.version };
            await client.http.POST(
              "/api/v1/mcp-connections/{connection_id}/{action}",
              {
                params: {
                  path: { connection_id: basis.id, action },
                  header: commandHeaders(
                    workspace.id,
                    key.forBody({ action, ...body }),
                  ),
                },
                body,
              },
            );
            done();
          }}
        />
        <Confirm
          title={t("Delete MCP connection")}
          description={t(
            "This clears local credentials and removes the connection. Remote registration cleanup is best effort.",
          )}
          trigger={t("Delete")}
          danger
          action={async () => {
            const query = { expected_version: basis.version };
            const result = data(
              await client.http.DELETE(
                "/api/v1/mcp-connections/{connection_id}",
                {
                  params: {
                    path: { connection_id: basis.id },
                    query,
                    header: commandHeaders(
                      workspace.id,
                      key.forBody({ delete: basis.id, ...query }),
                    ),
                  },
                },
              ),
            );
            onCleanup(result);
            done();
          }}
        />
      </div>
    </div>
  );
}
