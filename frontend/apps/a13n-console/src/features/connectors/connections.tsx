import { ResourceModalTitle } from "../../shared/resource-modal-title";
import { ConfigurationSummary } from "../../shared/configuration-summary";
import {
  Button,
  FormField,
  Input,
  ModalFrame,
  Tabs,
  TabsList,
  TabsPanel,
  TabsTab,
} from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import {
  useResourceEditorState,
  type ResourceEditorControl,
} from "../../shared/resource-modal";
import { ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import { Confirm } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";
import { ConnectionSetup } from "./setup";

export function ConnectionDetails({
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
    { can, workspace } = useWorkspace(),
    { t } = useTranslation(),
    [generation, setGeneration] = useState(0);
  const { open, setOpen, modalProps } = useResourceEditorState({
    controlledOpen,
    onClose,
    finalFocus,
  });
  const query = useQuery({
    queryKey: ["connector-connections", workspace.id, connectionId],
    enabled: open,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/connector-connections/{connection_id}", {
          params: { path: { connection_id: connectionId } },
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
          <Button size="sm" variant="outline" type="button">
            {t("Details")}
          </Button>
        ) : undefined
      }
      size={"md"}
      title={
        <ResourceModalTitle
          name={query.data?.name ?? t("Connection")}
          id={connectionId}
        />
      }
      description={
        <span className="flex flex-wrap items-center gap-2">
          <span>{query.data?.connector_key}</span>
          <StateBadge state={query.data?.status ?? "pending"} />
        </span>
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
              {can("connector_connection.manage") ? (
                <Tabs
                  className="gap-5"
                  key={generation}
                  defaultValue={
                    ["pending", "action_required"].includes(query.data.status)
                      ? "setup"
                      : "details"
                  }
                >
                  <TabsList aria-label={t("Connection details")}>
                    <TabsTab value={"details"}>{t("Details")}</TabsTab>
                    <TabsTab value={"setup"}>{t("Authorization")}</TabsTab>
                  </TabsList>
                  <TabsPanel value="details" keepMounted>
                    {
                      <ConnectionSettings
                        onCleanup={onCleanup}
                        initial={query.data}
                        close={() => setOpen(false)}
                        reload={reload}
                      />
                    }
                  </TabsPanel>
                  <TabsPanel value={"setup"}>
                    {<ConnectionSetup connection={query.data} />}
                  </TabsPanel>
                </Tabs>
              ) : (
                <div className={styles.stack}>
                  <ConfigurationSummary value={query.data.safe_metadata} />{" "}
                </div>
              )}
            </div>
          )
        ))}
    </ModalFrame>
  );
}
function ConnectionSettings({
  initial,
  close,
  reload,
  onCleanup,
}: {
  onCleanup: (receipt: Schema["ConnectionCleanupReceipt"]) => void;
  initial: Schema["ConnectorConnection"];
  close: () => void;
  reload: () => Promise<void>;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation(),
    key = useIdempotency(),
    basis = initial,
    [name, setName] = useState(initial.name);
  function done() {
    void cache.invalidateQueries({ queryKey: ["connector-connections"] });
    close();
  }
  const save = useMutation({
    mutationFn: () =>
      client.http
        .PATCH("/api/v1/connector-connections/{connection_id}", {
          params: { path: { connection_id: basis.id } },
          body: { name, expected_version: basis.version },
        })
        .then(data),
    onSuccess: done,
  });
  const body = { expected_version: basis.version };
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
      {Object.keys(basis.safe_metadata).length > 0 && (
        <ConfigurationSummary value={basis.safe_metadata} />
      )}

      <section className="flex flex-wrap items-center gap-3 rounded-lg bg-muted/40 p-3">
        <h3 className="mr-auto text-sm font-medium">
          {t("Connection actions")}
        </h3>
        {basis.status === "disabled" && (
          <p className="order-last w-full text-sm text-muted-foreground">
            {t(
              "Enabling requires completed authorization. Open the Authorization tab to finish setup if needed.",
            )}
          </p>
        )}
        <div className={styles.actions}>
          <Confirm
            subject={basis.name}
            retry={() =>
              void cache.invalidateQueries({
                queryKey: ["connector-connections"],
              })
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
              const action = basis.status === "disabled" ? "enable" : "disable";
              data(
                await client.http.POST(
                  "/api/v1/connector-connections/{connection_id}/{action}",
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
          {(["revoke", "delete"] as const).map((action) => (
            <Confirm
              subject={basis.name}
              retry={() =>
                void cache.invalidateQueries({
                  queryKey: ["connector-connections"],
                })
              }
              key={action}
              title={t(
                action === "revoke"
                  ? "Revoke authorization"
                  : "Delete connection",
              )}
              description={t(
                "Local access is disabled immediately. The result reports whether external cleanup succeeded.",
              )}
              trigger={t(action === "revoke" ? "Revoke" : "Delete")}
              danger
              triggerVariant="outline"
              action={async () => {
                const header = commandHeaders(
                  workspace.id,
                  key.forBody({ action, ...body }),
                );
                const result =
                  action === "revoke"
                    ? data(
                        await client.http.POST(
                          "/api/v1/connector-connections/{connection_id}/revoke",
                          {
                            params: {
                              path: { connection_id: basis.id },
                              header,
                            },
                            body,
                          },
                        ),
                      )
                    : data(
                        await client.http.DELETE(
                          "/api/v1/connector-connections/{connection_id}",
                          {
                            params: {
                              path: { connection_id: basis.id },
                              header,
                              query: body,
                            },
                          },
                        ),
                      );
                onCleanup(result);
                done();
              }}
            />
          ))}
        </div>
      </section>
    </div>
  );
}
