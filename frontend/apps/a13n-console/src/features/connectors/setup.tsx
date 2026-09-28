import { Button, FormField, Input } from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, rowTag, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import { SchemaFields, withSchemaValues } from "../../shared/forms";
import { startBrowserAuthorization } from "../connections/authorization-context";
import styles from "../../shared/shared.module.css";
import { jsonObject, validateSettings } from "../../shared/forms";
import { ConnectorToolPicker, MAX_TOOLS } from "./tool-picker";

type SetupTarget =
  | { connection: Schema["Connection"]; connector?: never; provider?: never }
  | {
      connection?: undefined;
      connector: Schema["ConnectorApp"];
      provider: Schema["Provider"];
    };
export function ConnectionSetup({
  connection,
  connector,
  provider,
  onStarted,
}: SetupTarget & { onStarted?: () => void }) {
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const created = useRef(connection);
  const saved =
    connection && "app" in connection.config ? connection.config : undefined;
  const [name, setName] = useState(connection?.name ?? connector?.name ?? ""),
    [setup, setSetup] = useState<Record<string, unknown>>(saved?.setup ?? {}),
    [picked, setPicked] = useState(saved?.actions);
  const providerId = connection?.connector_provider_id ?? provider!.id;
  const app = saved?.app ?? connector!.key;
  const appPath = { provider_id: providerId, app };
  const definition = useQuery({
    queryKey: ["connector-setup-catalog", workspace.id, providerId, app],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/connector-providers/{provider_id}/apps/{app}", {
          params: { path: appPath },
          signal,
        })
        .then(data),
  });
  const catalog = useQuery({
    queryKey: ["connector-actions", workspace.id, providerId, app],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/connector-providers/{provider_id}/apps/{app}/actions", {
          params: { path: appPath },
          signal,
        })
        .then(data),
  });
  // A new connection offers every tool while they fit; a larger app needs a choice.
  const tools =
    picked ??
    (catalog.data && catalog.data.items.length <= MAX_TOOLS
      ? catalog.data.items.map((tool) => tool.name)
      : []);
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
      if (!tools.length) throw new Error(t("Select at least one tool."));
      onStarted?.();
      const config = {
        app,
        actions: tools,
        setup: jsonObject(JSON.stringify(configured)),
      };
      let target = created.current;
      if (!target) {
        target = data(
          await client.workspace(workspace.id).POST("/api/v1/connections", {
            body: {
              type: provider!.type,
              name,
              config,
              auth: "account",
              connector_provider_id: providerId,
            },
          }),
        );
        created.current = target;
      } else {
        const current = data(
          await client
            .workspace(target.workspace_id)
            .GET("/api/v1/connections/{connection_id}", {
              params: { path: { connection_id: target.id } },
            }),
        );
        // Setup belongs to the configuration; an unchanged setup keeps the current account.
        target = data(
          await client
            .workspace(current.workspace_id)
            .PATCH("/api/v1/connections/{connection_id}", {
              params: { path: { connection_id: current.id } },
              headers: ifMatch(rowTag(current)),
              body: { config },
            }),
        );
        created.current = target;
      }
      return startBrowserAuthorization(client, target, basePath);
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
            <>
              <fieldset disabled={launch.isPending}>
                <SchemaFields
                  schema={definition.data.setup_schema}
                  value={setup}
                  onChange={setSetup}
                />
              </fieldset>
              {catalog.isPending ? (
                <Loading variant="list" rows={3} />
              ) : catalog.error ? (
                <ErrorNotice
                  error={catalog.error}
                  retry={() => void catalog.refetch()}
                />
              ) : (
                <ConnectorToolPicker
                  catalog={catalog.data.items}
                  value={tools}
                  disabled={launch.isPending}
                  onChange={setPicked}
                />
              )}
            </>
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
