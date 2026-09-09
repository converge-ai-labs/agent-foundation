import {
  Button,
  DisclosureSection,
  FormField,
  Input,
  ModalFrame,
} from "a13n-ui";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { Empty, ErrorNotice } from "../../shared/feedback";
import { FormActions, JsonView } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";
import { ConnectionSetup } from "./setup";

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
    <ModalFrame
      onOpenChange={(value) => {
        setOpen(value);
        if (!value) setSelected(undefined);
      }}
      trigger={
        <Button
          size="sm"
          variant="outline"
          onClick={() => {
            if (!discover.data) discover.mutate();
          }}
          type="button"
        >
          {t("Discover connectors")}
        </Button>
      }
      size={"md"}
      title={t("Discover connectors")}
      description={provider.name}
      closeLabel={t("Close")}
      open={open}
    >
      <div className={styles.stack}>
        {selected ? (
          <>
            <Button
              variant="outline"
              onClick={() => setSelected(undefined)}
              type="button"
            >
              {t("Back to connectors")}
            </Button>
            <ConnectConnector connector={selected} />
          </>
        ) : (
          <>
            <div className={styles.toolbar}>
              <FormField
                className="min-w-0 w-full"
                label={t("Search connectors")}
                hideLabel={true}
              >
                <Input
                  placeholder={t("Search connectors")}
                  value={search}
                  onChange={(event) => {
                    setSearch(event.target.value);
                    setPage(0);
                  }}
                  type="search"
                />
              </FormField>
              <Button
                variant="outline"
                loading={discover.isPending}
                onClick={() => discover.mutate()}
                type="button"
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
                      variant="default"
                      size="sm"
                      onClick={() => setSelected(item)}
                      type="button"
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
                variant="outline"
                disabled={page === 0}
                onClick={() => setPage((value) => value - 1)}
                type="button"
              >
                {t("Previous")}
              </Button>
              <Button
                variant="outline"
                disabled={(page + 1) * 10 >= matches.length}
                onClick={() => setPage((value) => value + 1)}
                type="button"
              >
                {t("Next")}
              </Button>
            </div>
          </>
        )}
      </div>
    </ModalFrame>
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
    <ModalFrame
      trigger={
        <Button
          size="sm"
          variant="outline"
          loading={preview.isPending}
          onClick={() => preview.mutate()}
          type="button"
        >
          {t("Preview tools")}
        </Button>
      }
      size={"md"}
      title={t("Tool preview")}
      description={t(
        "Available tools are advisory until the external account is authorized.",
      )}
      closeLabel={t("Close")}
    >
      <ErrorNotice error={preview.error} />
      {preview.data && (
        <div className={styles.stack}>
          {preview.data.items.map((tool) => (
            <DisclosureSection key={tool.key} title={<>{tool.key}</>}>
              <JsonView value={tool} />
            </DisclosureSection>
          ))}
        </div>
      )}
    </ModalFrame>
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
        .POST("/api/v1/workspaces/{workspace}/connector-connections", {
          params: {
            path: { workspace: workspace.id },
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
      <FormField className="min-w-0 w-full" label={t("Connection name")}>
        <Input
          required={true}
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={128}
        />
      </FormField>
      <ErrorNotice error={create.error} />
      <FormActions pending={create.isPending} label={t("Create connection")} />
    </form>
  );
}
