import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, Dialog, Input, SearchInput } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { ErrorNotice, Empty } from "../../shared/feedback";
import { FormActions, JsonView } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import { ConnectionSetup } from "./setup";
import styles from "../../shared/shared.module.css";

export function ConnectorCatalog({
  provider,
}: {
  provider: Schema["ConnectorProvider"];
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { can } = useWorkspace(),
    [open, setOpen] = useState(false),
    [search, setSearch] = useState(""),
    [page, setPage] = useState(0),
    [selected, setSelected] = useState<Schema["Connector"]>();
  const discover = useMutation({
    mutationFn: () =>
      client.http
        .POST(
          "/api/v1/connector-providers/{connector_provider_id}/discover-connectors",
          { params: { path: { connector_provider_id: provider.id } } },
        )
        .then(data),
  });
  const matches =
    discover.data?.items.filter((item) =>
      `${item.name} ${item.key}`
        .toLocaleLowerCase()
        .includes(search.toLocaleLowerCase()),
    ) ?? [];
  return (
    <Dialog
      title={t("Discover connectors")}
      description={provider.name}
      closeLabel={t("Close")}
      open={open}
      onOpenChange={(value) => {
        setOpen(value);
        if (!value) setSelected(undefined);
      }}
      trigger={
        <Button
          size="sm"
          onClick={() => {
            if (!discover.data) discover.mutate();
          }}
        >
          {t("Discover connectors")}
        </Button>
      }
    >
      <div className={styles.stack}>
        {selected ? (
          <>
            <Button onClick={() => setSelected(undefined)}>
              {t("Back to connectors")}
            </Button>
            <ConnectConnector connector={selected} />
          </>
        ) : (
          <>
            <div className={styles.toolbar}>
              <SearchInput
                label={t("Search connectors")}
                placeholder={t("Search connectors")}
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setPage(0);
                }}
              />
              <Button
                loading={discover.isPending}
                onClick={() => discover.mutate()}
              >
                {t("Refresh discovery")}
              </Button>
            </div>
            <ErrorNotice error={discover.error} />
            {matches.slice(page * 10, (page + 1) * 10).map((item) => (
              <article key={item.key} className={styles.card}>
                <h3>{item.name}</h3>
                <p className={styles.muted}>{item.description}</p>
                <small>{item.authentication_methods.join(", ")}</small>
                <div className={styles.actions}>
                  <ToolPreview connector={item} />
                  {can("connector_connection.manage") && (
                    <Button
                      variant="primary"
                      size="sm"
                      onClick={() => setSelected(item)}
                    >
                      {t("Connect")}
                    </Button>
                  )}
                </div>
              </article>
            ))}
            {discover.data && !matches.length && (
              <Empty
                title={t("No matching connectors")}
                description={t("Try another search or refresh discovery.")}
              />
            )}
            <div className={styles.pagination}>
              <Button
                disabled={page === 0}
                onClick={() => setPage((value) => value - 1)}
              >
                {t("Previous")}
              </Button>
              <Button
                disabled={(page + 1) * 10 >= matches.length}
                onClick={() => setPage((value) => value + 1)}
              >
                {t("Next")}
              </Button>
            </div>
          </>
        )}
      </div>
    </Dialog>
  );
}
function ToolPreview({ connector }: { connector: Schema["Connector"] }) {
  const client = useClient(),
    { t } = useTranslation();
  const preview = useMutation({
    mutationFn: () =>
      client.http
        .GET(
          "/api/v1/connector-providers/{connector_provider_id}/connectors/{connector_key}/tools",
          {
            params: {
              path: {
                connector_provider_id: connector.connector_provider_id,
                connector_key: connector.key,
              },
            },
          },
        )
        .then(data),
  });
  return (
    <Dialog
      title={t("Tool preview")}
      description={t(
        "Available tools are advisory until the external account is authorized.",
      )}
      closeLabel={t("Close")}
      trigger={
        <Button
          size="sm"
          onClick={() => preview.mutate()}
          loading={preview.isPending}
        >
          {t("Preview tools")}
        </Button>
      }
    >
      <ErrorNotice error={preview.error} />
      {preview.data && (
        <div className={styles.stack}>
          {preview.data.items.map((tool) => (
            <details key={tool.key}>
              <summary>{tool.key}</summary>
              <JsonView value={tool} />
            </details>
          ))}
        </div>
      )}
    </Dialog>
  );
}
function ConnectConnector({ connector }: { connector: Schema["Connector"] }) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    key = useIdempotency(),
    [name, setName] = useState(connector.name),
    [created, setCreated] = useState<Schema["ConnectorConnection"]>();
  const create = useMutation({
    mutationFn: () => {
      const body = {
        connector_provider_id: connector.connector_provider_id,
        connector_key: connector.key,
        name,
      };
      return client.http
        .POST("/api/v1/workspaces/{workspace_id}/connector-connections", {
          params: {
            path: { workspace_id: workspace.id },
            header: commandHeaders(workspace.id, key.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: (result) => {
      setCreated(result);
      void cache.invalidateQueries({ queryKey: ["connector-connections"] });
    },
  });
  return created ? (
    <ConnectionSetup connection={created} connector={connector} />
  ) : (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate();
      }}
    >
      <h3>{connector.name}</h3>
      <Input
        label={t("Connection name")}
        value={name}
        onChange={(event) => setName(event.target.value)}
        required
        maxLength={128}
      />
      <ErrorNotice error={create.error} />
      <FormActions pending={create.isPending} label={t("Create connection")} />
    </form>
  );
}
