import { ResourceModalTitle } from "../../shared/resource-modal-title";
import {
  useResourceEditorState,
  type ResourceEditorControl,
} from "../../shared/resource-modal";
import { ResourceEditorButton } from "../../shared/resource-editor-button";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FormField,
  Button,
  Input,
  ReadOnlyField,
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
import { Confirm } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";
import { MCPAuthorization } from "./authorization";
import { MCPTools } from "./tools";

export function MCPEditor({
  connectionId,
  onCleanup,
  controlledOpen,
  onClose,
  finalFocus,
}: ResourceEditorControl & {
  connectionId: string;
  onCleanup: (receipt: Schema["ConnectionCleanupReceipt"]) => void;
}) {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    [generation, setGeneration] = useState(0),
    id = connectionId;
  const { open, setOpen, modalProps } = useResourceEditorState({
    controlledOpen,
    onClose,
    finalFocus,
  });
  const query = useQuery({
    queryKey: ["mcp-connections", workspace.id, id],
    enabled: open,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/mcp-connections/{connection_id}", {
          params: { path: { connection_id: id } },
          signal,
        })
        .then(data)
        .then((connection) => {
          if (connection.workspace_id !== workspace.id)
            throw new Error(t("Connection belongs to another workspace."));
          return connection;
        }),
  });
  async function reload() {
    await query.refetch();
    setGeneration((value) => value + 1);
  }
  return (
    <ModalFrame
      {...modalProps}
      trigger={
        controlledOpen === undefined ? (
          <ResourceEditorButton editing createLabel="" editLabel="Details" />
        ) : undefined
      }
      size={"md"}
      title={
        query.data ? (
          <ResourceModalTitle name={query.data.name} id={query.data.id} />
        ) : (
          t("MCP connection")
        )
      }
      description={
        query.data && (
          <span className="flex flex-wrap items-center gap-2">
            <span className="min-w-0 break-all">{query.data.endpoint_url}</span>
            <StateBadge state={query.data.status} />
          </span>
        )
      }
      closeLabel={t("Close")}
    >
      {open &&
        (query.isPending ? (
          <Loading />
        ) : query.error ? (
          <ErrorNotice error={query.error} />
        ) : (
          query.data && (
            <div className={styles.stack}>
              {query.data.status_reason && (
                <p className={styles.muted}>
                  {t(`state.${query.data.status_reason}`)}
                </p>
              )}
              {can("mcp_connection.manage") ? (
                <Tabs
                  className="gap-5"
                  key={generation}
                  defaultValue={
                    ["pending", "action_required"].includes(query.data.status)
                      ? "authorization"
                      : "details"
                  }
                >
                  <TabsList aria-label={t("MCP connection")}>
                    <TabsTab value={"details"}>{t("Details")}</TabsTab>
                    <TabsTab value={"authorization"}>
                      {t("Authorization")}
                    </TabsTab>
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
                <div className={styles.stack}>
                  <ReadOnlyField label={t("Authentication")}>
                    {t(`auth.${query.data.auth_mode}`)}
                  </ReadOnlyField>
                  <ReadOnlyField label={t("Credentials")}>
                    {t(
                      query.data.credential_configured
                        ? "Configured"
                        : "Not configured",
                    )}
                  </ReadOnlyField>{" "}
                </div>
              )}
            </div>
          )
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
    basis = initial,
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
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <div className="flex items-end gap-3">
          <FormField className="min-w-0 w-full" label={t("Name")}>
            <Input
              required={true}
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={128}
            />
          </FormField>
          <Button
            type="submit"
            variant="outline"
            loading={save.isPending}
            disabled={name === basis.name}
          >
            {t("Save")}
          </Button>
        </div>
        <ErrorNotice error={save.error} retry={() => void reload()} />
      </form>
      <div className="flex items-center justify-between gap-3 text-sm">
        <span className="text-muted-foreground">{t("Authentication")}</span>
        <span>{t(`auth.${basis.auth_mode}`)}</span>
      </div>

      <section className="flex flex-wrap items-center gap-3 rounded-lg bg-muted/40 p-3">
        <h3 className="mr-auto text-sm font-medium">
          {t("Connection actions")}
        </h3>
        {basis.status === "disabled" &&
          basis.auth_mode !== "none" &&
          !basis.credential_configured && (
            <p className="text-sm text-muted-foreground">
              {t(
                "Enabling requires completed authorization. Open the Authorization tab to finish setup if needed.",
              )}
            </p>
          )}
        <div className={styles.actions}>
          <Confirm
            retry={() =>
              void cache.invalidateQueries({ queryKey: ["mcp-connections"] })
            }
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
              data(
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
                ),
              );
              done();
            }}
          />
          <Confirm
            retry={() =>
              void cache.invalidateQueries({ queryKey: ["mcp-connections"] })
            }
            title={t("Delete MCP connection")}
            description={t(
              "This clears local credentials and removes the connection. Remote registration cleanup is best effort.",
            )}
            trigger={t("Delete")}
            danger
            triggerVariant="destructive"
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
      </section>
    </div>
  );
}
