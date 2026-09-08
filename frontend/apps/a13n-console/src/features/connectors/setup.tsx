import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { ErrorNotice, StateBadge } from "../../shared/feedback";
import { FormActions } from "../../shared/form";
import { SchemaFields } from "../../shared/schema-fields";
import { AuthorizationLink } from "../../shared/authorization-link";
import { jsonObject, validateSettings } from "../../shared/validation";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";

export function ConnectionSetup({
  connection,
  connector,
}: {
  connection: Schema["ConnectorConnection"];
  connector?: Schema["Connector"];
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    key = useIdempotency(),
    [basis] = useState(connection),
    [setup, setSetup] = useState<Record<string, unknown>>({});
  const catalog = useQuery({
    queryKey: ["connector-setup-catalog", connection.connector_provider_id],
    enabled: !connector,
    queryFn: () =>
      client.http
        .POST(
          "/api/v1/connector-providers/{connector_provider_id}/discover-connectors",
          {
            params: {
              path: { connector_provider_id: connection.connector_provider_id },
            },
          },
        )
        .then(data),
  });
  const definition =
    connector ??
    catalog.data?.items.find((item) => item.key === connection.connector_key);
  const launch = useMutation({
    gcTime: 0,
    mutationFn: () => {
      if (!definition)
        throw new Error(t("This connector is unavailable from its provider."));
      validateSettings(definition.setup_schema, setup);
      const body = {
          expected_version: basis.version,
          setup: jsonObject(JSON.stringify(setup)),
          return_path: `/workspaces/${workspace.id}/connectors`,
        },
        params = {
          path: { connection_id: basis.id },
          header: commandHeaders(workspace.id, key.forBody(body)),
        };
      return basis.status === "pending"
        ? client.http
            .POST("/api/v1/connector-connections/{connection_id}/setup", {
              params,
              body,
            })
            .then(data)
        : client.http
            .POST("/api/v1/connector-connections/{connection_id}/reconnect", {
              params,
              body,
            })
            .then(data);
    },
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["connector-connections"] });
    },
  });
  const status = useQuery({
    queryKey: [
      "connector-connections",
      workspace.id,
      connection.id,
      "setup-status",
    ],
    enabled: !!launch.data,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/connector-connections/{connection_id}", {
          params: { path: { connection_id: connection.id } },
          signal,
        })
        .then(data),
    refetchInterval: (query) =>
      launch.data &&
      Date.now() < Date.parse(launch.data.expires_at) &&
      query.state.data?.status === "pending"
        ? 3000
        : false,
  });
  return (
    <div className={styles.stack}>
      <h3>{connection.name}</h3>
      <p className={styles.muted}>
        {t(
          "Authorize the external account in the provider-hosted flow. Credentials stay with the provider.",
        )}
      </p>
      <ErrorNotice error={catalog.error ?? launch.error ?? status.error} />
      {launch.data ? (
        <>
          <StateBadge
            state={status.data?.status ?? launch.data.connection.status}
          />
          {status.data?.status !== "ready" && (
            <AuthorizationLink
              url={launch.data.redirect_url}
              expiresAt={launch.data.expires_at}
            />
          )}
          <Button onClick={() => void status.refetch()}>
            {t("Refresh connection")}
          </Button>
        </>
      ) : (
        <form
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault();
            launch.mutate();
          }}
        >
          {definition && (
            <SchemaFields
              schema={definition.setup_schema}
              value={setup}
              onChange={setSetup}
            />
          )}
          <FormActions
            pending={launch.isPending}
            label={t("Start authorization")}
          />
        </form>
      )}
    </div>
  );
}
