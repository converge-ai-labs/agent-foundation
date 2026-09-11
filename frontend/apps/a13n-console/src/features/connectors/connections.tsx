import { ResourceModalTitle } from "../../shared/resource-modal-title";
import { ConfigurationSummary } from "../../shared/configuration-summary";
import { PageActions } from "../../shared/page-actions";
import { ManageProvidersLink } from "../providers/manage-link";
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
import { PlugIcon } from "@phosphor-icons/react";
import { useState } from "react";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import {
  Pagination,
  ResourceIdentity,
  ResourceTable,
  useCursor,
} from "../../shared/collection";
import { Empty, ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import { Confirm, JsonView } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";
import { ConnectionSetup } from "./setup";

export function ConnectorConnections() {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation(),
    page = useCursor(),
    [cleanup, setCleanup] = useState<Schema["ConnectionCleanupReceipt"]>();
  const query = useQuery({
    queryKey: ["connector-connections", workspace.id, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/connector-connections", {
          params: {
            path: { workspace: workspace.id },
            query: { cursor: page.cursor },
          },
          signal,
        })
        .then(data),
  });
  return (
    <div className={styles.stack}>
      <PageActions>
        <ManageProvidersLink category="connectors" scope="workspace" />
      </PageActions>
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
                tone: "primary",
                render: (item) => (
                  <ResourceIdentity
                    name={item.name}
                    description={item.connector_key}
                    resourceId={item.id}
                    icon={<PlugIcon size={17} />}
                  />
                ),
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
                  <ConnectionDetails connection={item} onCleanup={setCleanup} />
                ),
              },
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        !query.error && (
          <Empty
            title={t("No connector connections")}
            description={t(
              "Open Providers, discover a connector, and authorize an account to get started.",
            )}
          />
        )
      )}
    </div>
  );
}
function ConnectionDetails({
  connection,
  onCleanup,
}: {
  connection: Schema["ConnectorConnection"];
  onCleanup: (receipt: Schema["ConnectionCleanupReceipt"]) => void;
}) {
  const client = useClient(),
    { can } = useWorkspace(),
    { t } = useTranslation(),
    [open, setOpen] = useState(false),
    [generation, setGeneration] = useState(0);
  const query = useQuery({
    queryKey: ["connector-connections", connection.workspace_id, connection.id],
    enabled: open,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/connector-connections/{connection_id}", {
          params: { path: { connection_id: connection.id } },
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
      onOpenChange={setOpen}
      trigger={
        <Button size="sm" variant="outline" type="button">
          {t("Details")}
        </Button>
      }
      size={"md"}
      title={
        <ResourceModalTitle
          name={query.data?.name ?? connection.name}
          id={connection.id}
        />
      }
      description={
        <span className="flex flex-wrap items-center gap-2">
          <span>{query.data?.connector_key ?? connection.connector_key}</span>
          <StateBadge state={query.data?.status ?? connection.status} />
        </span>
      }
      closeLabel={t("Close")}
      open={open}
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
                <Tabs key={generation} defaultValue="details">
                  <TabsList aria-label={t("Connection details")}>
                    <TabsTab value={"details"}>{t("Details")}</TabsTab>
                    <TabsTab value={"setup"}>{t("Authorization")}</TabsTab>
                  </TabsList>
                  <TabsPanel value={"details"}>
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
    [basis] = useState(initial),
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

      <section className="grid gap-3 border-t border-border pt-4">
        <h3 className="text-sm font-medium">{t("Connection actions")}</h3>
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
              const action = basis.status === "disabled" ? "enable" : "disable";
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
              );
              done();
            }}
          />
          {(["revoke", "delete"] as const).map((action) => (
            <Confirm
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
