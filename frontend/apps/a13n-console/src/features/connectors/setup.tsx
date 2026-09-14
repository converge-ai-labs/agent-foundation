import { Button, FormField, Input } from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions } from "../../shared/form";
import { SchemaFields, withSchemaValues } from "../../shared/schema-fields";
import { useIdempotency } from "../../shared/idempotency";
import { startBrowserAuthorization } from "../connections/authorization-context";
import styles from "../../shared/shared.module.css";
import { jsonObject, validateSettings } from "../../shared/validation";

type SetupTarget =
  | { connection: Schema["Connection"]; connector?: Schema["Connector"] }
  | { connection?: undefined; connector: Schema["Connector"] };
export function ConnectionSetup({
  connection,
  connector,
  onStarted,
}: SetupTarget & { onStarted?: () => void }) {
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    key = useIdempotency();
  const created = useRef(connection);
  const [name, setName] = useState(connection?.name ?? connector?.name ?? ""),
    [setup, setSetup] = useState<Record<string, unknown>>({});
  const source = connection?.source;
  const providerId =
    source?.kind === "connector"
      ? source.provider_id
      : connector!.connector_provider_id;
  const connectorKey =
    source?.kind === "connector" ? source.connector_key : connector!.key;
  const definition = useQuery({
    queryKey: [
      "connector-setup-catalog",
      workspace.id,
      providerId,
      connectorKey,
    ],
    queryFn: ({ signal }) =>
      client.http
        .GET(
          "/api/v1/connector-providers/{connector_provider_id}/connectors/{connector_key}",
          {
            params: {
              path: {
                connector_provider_id: providerId,
                connector_key: connectorKey,
              },
            },
            signal,
          },
        )
        .then(data),
  });
  const launch = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const selected = definition.data;
      if (
        !selected ||
        selected.unavailable_reason ||
        !selected.authentication_methods.length
      )
        throw new Error(
          selected?.unavailable_reason ??
            t("This connector is unavailable from its provider."),
        );
      const configured = withSchemaValues(selected.setup_schema, setup);
      validateSettings(selected.setup_schema, configured);
      onStarted?.();
      let target = created.current;
      if (!target) {
        const body: Schema["CreateConnectionRequest"] = {
          name,
          source: {
            kind: "connector",
            provider_id: providerId,
            connector_key: connectorKey,
          },
        };
        target = data(
          await client.http.POST("/api/v1/workspaces/{workspace}/connections", {
            params: {
              path: { workspace: workspace.id },
              header: commandHeaders(workspace.id, key.forBody(body)),
            },
            body,
          }),
        );
        created.current = target;
      } else {
        target = data(
          await client.http.GET("/api/v1/connections/{connection_id}", {
            params: { path: { connection_id: target.id } },
          }),
        );
        created.current = target;
      }
      return startBrowserAuthorization(
        client,
        target,
        basePath,
        jsonObject(JSON.stringify(configured)),
      );
    },
    onSettled: () => {
      void cache.invalidateQueries({ queryKey: ["connections"] });
    },
  });
  return (
    <div className={styles.stack}>
      <p className={styles.muted}>
        {t("Authorize the external account with your provider.")}
      </p>
      <ErrorNotice error={definition.error ?? launch.error} />
      {definition.isPending ? (
        <Loading variant="form" rows={3} />
      ) : (
        <form
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault();
            launch.mutate();
          }}
        >
          {!connection && (
            <FormField label={t("Connection name")}>
              <Input
                required
                value={name}
                disabled={!!created.current || launch.isPending}
                maxLength={128}
                onChange={(event) => setName(event.target.value)}
              />
            </FormField>
          )}
          {definition.data && !definition.data.unavailable_reason && (
            <fieldset disabled={launch.isPending}>
              <SchemaFields
                schema={definition.data.setup_schema}
                value={setup}
                onChange={setSetup}
              />
            </fieldset>
          )}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            loading={definition.isFetching}
            disabled={launch.isPending}
            onClick={() => void definition.refetch()}
          >
            {t("Refresh configurations")}
          </Button>
          {definition.data?.unavailable_reason ? (
            <p role="status">{t(definition.data.unavailable_reason)}</p>
          ) : (
            definition.data && (
              <FormActions
                pending={launch.isPending}
                label={t(created.current ? "Authorize connection" : "Connect")}
              />
            )
          )}
        </form>
      )}
    </div>
  );
}
