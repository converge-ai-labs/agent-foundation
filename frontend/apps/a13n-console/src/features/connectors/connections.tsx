import { Plug } from "lucide-react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Dialog, Input, Tabs } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { ErrorNotice, Empty, Loading, StateBadge } from "../../shared/feedback";
import { Confirm, FormActions, JsonView } from "../../shared/form";
import {
  Table,
  Pagination,
  ResourceIdentity,
  useCursor,
} from "../../shared/collection";
import { useIdempotency } from "../../shared/idempotency";
import { ConnectionSetup } from "./setup";
import styles from "../../shared/shared.module.css";

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
        .GET("/api/v1/workspaces/{workspace_id}/connector-connections", {
          params: {
            path: { workspace_id: workspace.id },
            query: { cursor: page.cursor },
          },
          signal,
        })
        .then(data),
  });
  return (
    <div className={styles.stack}>
      {cleanup && (
        <div role="status">
          <h3>{t("Cleanup result")}</h3>
          <JsonView value={cleanup} />
          <Button size="sm" onClick={() => setCleanup(undefined)}>
            {t("Dismiss")}
          </Button>
        </div>
      )}
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : query.data?.items.length ? (
        <>
          <Table
            items={query.data.items}
            columns={[
              {
                label: t("Connection"),
                render: (item) => (
                  <ResourceIdentity
                    name={item.name}
                    description={item.connector_key}
                    icon={<Plug size={17} />}
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
    <Dialog
      title={connection.name}
      description={t(
        "Manage this workspace connection and its external authorization.",
      )}
      closeLabel={t("Close")}
      open={open}
      onOpenChange={setOpen}
      trigger={<Button size="sm">{t("Details")}</Button>}
    >
      {open &&
        (query.isPending ? (
          <Loading />
        ) : query.error ? (
          <ErrorNotice error={query.error} />
        ) : (
          query.data &&
          (can("connector_connection.manage") ? (
            <Tabs
              key={generation}
              label={t("Connection details")}
              defaultValue="details"
              items={[
                {
                  value: "details",
                  label: t("Details"),
                  content: (
                    <ConnectionSettings
                      onCleanup={onCleanup}
                      initial={query.data}
                      close={() => setOpen(false)}
                      reload={reload}
                    />
                  ),
                },
                {
                  value: "setup",
                  label: t("Authorization"),
                  content: <ConnectionSetup connection={query.data} />,
                },
              ]}
            />
          ) : (
            <JsonView value={query.data} />
          ))
        ))}
    </Dialog>
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
      <StateBadge state={basis.status} />
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <Input
          label={t("Name")}
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
          maxLength={128}
        />
        <ErrorNotice error={save.error} retry={() => void reload()} />
        <FormActions pending={save.isPending} />
      </form>
      <details>
        <summary>{t("Account metadata")}</summary>
        <JsonView value={basis.safe_metadata} />
      </details>
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
                          params: { path: { connection_id: basis.id }, header },
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
    </div>
  );
}
