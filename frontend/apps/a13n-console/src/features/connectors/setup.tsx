import { Button } from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { AuthorizationLink } from "../../shared/authorization-link";
import { ErrorNotice, StateBadge } from "../../shared/feedback";
import { FormActions } from "../../shared/form";
import { SchemaFields, withSchemaConstants } from "../../shared/schema-fields";
import { useIdempotency } from "../../shared/idempotency";
import { createBrowserNonce, saveAuthorization } from "./authorization-context";
import styles from "../../shared/shared.module.css";
import { jsonObject, validateSettings } from "../../shared/validation";

export function ConnectionSetup({
  connection,
  connector,
}: {
  connection: Schema["ConnectorConnection"];
  connector?: Schema["Connector"];
}) {
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    key = useIdempotency(),
    [basis] = useState(connection),
    [nonce] = useState(createBrowserNonce),
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
    mutationFn: (options?: {
      restart: Schema["ConnectorConnection"];
      nonce: string;
    }) => {
      const selected = options?.restart ?? basis,
        browserNonce = options?.nonce ?? nonce;
      if (!definition)
        throw new Error(t("This connector is unavailable from its provider."));
      if (definition.authentication_methods.length === 0)
        throw new Error(
          t(
            "Configure an OAuth2 auth config in the provider before connecting.",
          ),
        );
      const configured = withSchemaConstants(definition.setup_schema, setup);
      validateSettings(definition.setup_schema, configured);
      const body = {
          expected_version: selected.version,
          browser_nonce: browserNonce,
          setup: jsonObject(JSON.stringify(configured)),
          return_path: `${basePath}/connectors`,
        },
        params = {
          path: { connection_id: selected.id },
          header: commandHeaders(workspace.id, key.forBody(body)),
        };
      saveAuthorization({
        browser_nonce: browserNonce,
        workspace_id: workspace.id,
        return_path: `${basePath}/connectors`,
        connection_id: selected.id,
      });
      return !options?.restart && basis.status === "pending"
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
    onSuccess: (result, options) => {
      if (result.requires_browser_callback)
        saveAuthorization({
          browser_nonce: options?.nonce ?? nonce,
          workspace_id: workspace.id,
          return_path: `${basePath}/connectors`,
          connection_id: basis.id,
          attempt_id: result.attempt_id,
          expires_at: result.expires_at,
        });
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
              sameTab={launch.data.requires_browser_callback}
            />
          )}
          {(!launch.data.redirect_url ||
            status.data?.status === "action_required" ||
            Date.now() >= Date.parse(launch.data.expires_at)) &&
            status.data?.status !== "ready" && (
              <Button
                loading={launch.isPending}
                onClick={() =>
                  launch.mutate({
                    restart: status.data ?? launch.data!.connection,
                    nonce: createBrowserNonce(),
                  })
                }
              >
                {t("Restart authorization")}
              </Button>
            )}
          <Button
            variant="outline"
            type="button"
            onClick={() => void status.refetch()}
          >
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
          {definition?.authentication_methods.length === 0 ? (
            <p role="status">
              {t(
                "Configure an OAuth2 auth config in the provider before connecting.",
              )}
            </p>
          ) : (
            <FormActions
              pending={launch.isPending}
              label={t("Start authorization")}
            />
          )}
        </form>
      )}
    </div>
  );
}
