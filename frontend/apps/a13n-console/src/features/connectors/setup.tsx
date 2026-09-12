import { ManageProvidersLink } from "../providers/manage-link";
import { ArrowLeftIcon } from "@phosphor-icons/react";
import { ApiError } from "@converge.ai/a13n";
import { Button, FormField, Input } from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import {
  AuthorizationLink,
  authorizationHref,
} from "../../shared/authorization-link";
import { ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import { FormActions } from "../../shared/form";
import { SchemaFields, withSchemaValues } from "../../shared/schema-fields";
import { useIdempotency } from "../../shared/idempotency";
import { createBrowserNonce, saveAuthorization } from "./authorization-context";
import styles from "../../shared/shared.module.css";
import { jsonObject, validateSettings } from "../../shared/validation";

type SetupTarget =
  | {
      connection: Schema["ConnectorConnection"];
      connector?: Schema["Connector"];
    }
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
  const restarted = useRef<{ version: number; nonce: string }>(undefined);
  const [current, setCurrent] = useState(connection),
    [name, setName] = useState(connection?.name ?? connector?.name ?? ""),
    [started, setStarted] = useState(false),
    [nonce] = useState(createBrowserNonce),
    [setup, setSetup] = useState<Record<string, unknown>>({});
  const providerId =
    connection?.connector_provider_id ?? connector!.connector_provider_id;
  const connectorKey = connection?.connector_key ?? connector!.key;
  const provider = useQuery({
    queryKey: ["connector-provider", providerId],
    enabled: connection?.status === "ready",
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/connector-providers/{connector_provider_id}", {
          params: { path: { connector_provider_id: providerId } },
          signal,
        })
        .then(data),
  });
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
    mutationFn: async (restart?: Schema["ConnectorConnection"]) => {
      const selected = definition.data;
      if (
        !selected ||
        selected.unavailable_reason ||
        selected.authentication_methods.length === 0
      )
        throw new Error(
          selected?.unavailable_reason ??
            t("This connector is unavailable from its provider."),
        );
      const configured = withSchemaValues(selected.setup_schema, setup);
      validateSettings(selected.setup_schema, configured);
      setStarted(true);
      onStarted?.();
      let target = restart ?? created.current;
      if (!target) {
        const body = {
          connector_provider_id: providerId,
          connector_key: connectorKey,
          name,
        };
        target = data(
          await client.http.POST(
            "/api/v1/workspaces/{workspace}/connector-connections",
            {
              params: {
                path: { workspace: workspace.id },
                header: commandHeaders(workspace.id, key.forBody(body)),
              },
              body,
            },
          ),
        );
        created.current = target;
        setCurrent(target);
        void cache.invalidateQueries({ queryKey: ["connector-connections"] });
      }
      if (restart && restarted.current?.version !== restart.version)
        restarted.current = {
          version: restart.version,
          nonce: createBrowserNonce(),
        };
      const browserNonce = restart ? restarted.current!.nonce : nonce;
      const returnPath = `${basePath}/connections`;
      const context = {
        browser_nonce: browserNonce,
        workspace_id: workspace.id,
        return_path: returnPath,
        connection_id: target.id,
        connection_name: target.name,
        workspace_name: workspace.name,
      };
      saveAuthorization(context);
      const body = {
        expected_version: target.version,
        browser_nonce: browserNonce,
        setup: jsonObject(JSON.stringify(configured)),
        return_path: returnPath,
      };
      const params = {
        path: { connection_id: target.id },
        header: commandHeaders(workspace.id, key.forBody(body)),
      };
      const result = data(
        await (restart || target.status !== "pending"
          ? client.http.POST(
              "/api/v1/connector-connections/{connection_id}/reconnect",
              { params, body },
            )
          : client.http.POST(
              "/api/v1/connector-connections/{connection_id}/setup",
              { params, body },
            )),
      );
      if (result.completion_method !== "polling")
        saveAuthorization({
          ...context,
          attempt_id: result.attempt_id,
          completion_method: result.completion_method,
          expires_at: result.expires_at,
        });
      const href = authorizationHref(result.redirect_url);
      if (href && result.completion_method !== "polling")
        window.location.assign(href);
      return result;
    },
    onSettled: () => {
      void cache.invalidateQueries({ queryKey: ["connector-connections"] });
    },
  });
  const status = useQuery({
    queryKey: [
      "connector-connections",
      workspace.id,
      current?.id,
      "setup-status",
    ],
    enabled: !!current,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/connector-connections/{connection_id}", {
          params: { path: { connection_id: current!.id } },
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
  const replacementRequired =
    (connection?.status === "ready" && provider.data?.type === "composio") ||
    (launch.error instanceof ApiError &&
      launch.error.code === "reconnect_unsupported");
  const setupAlreadyStarted =
    launch.error instanceof ApiError &&
    launch.error.code === "setup_already_started";
  return (
    <div className={styles.stack}>
      <p className={styles.muted}>
        {t(
          "Authorize the external account with your provider. Credentials stay with the provider.",
        )}
      </p>
      <ErrorNotice
        error={
          provider.error ??
          definition.error ??
          (replacementRequired || setupAlreadyStarted ? null : launch.error) ??
          status.error
        }
      />
      {definition.error instanceof ApiError &&
        [
          "connector_provider_disabled",
          "connector_unavailable",
          "connector_provider_unavailable",
        ].includes(definition.error.code) && (
          <ManageProvidersLink category="connectors" scope="workspace" />
        )}
      {replacementRequired ? (
        <p role="status">
          {t(
            "This provider cannot reauthorize an existing account. Create a new connection, authorize it, then select it in your agent settings.",
          )}{" "}
          <a
            className="inline-flex items-center gap-1.5"
            href={`${basePath}/connections`}
          >
            <ArrowLeftIcon size={14} aria-hidden="true" />{" "}
            {t("Back to connections")}
          </a>
        </p>
      ) : connection?.status === "ready" && provider.isPending ? (
        <Loading />
      ) : provider.error ? null : launch.error && current ? (
        <div className={styles.stack}>
          <StateBadge state={status.data?.status ?? current.status} />
          <p>
            {t(
              setupAlreadyStarted
                ? "Authorization has already started. Restarting invalidates the previous authorization link."
                : "Your connection is saved. Retry the authorization request or restart from its current status.",
            )}
          </p>
          {status.data?.status !== "ready" && (
            <>
              {!setupAlreadyStarted && (
                <Button
                  loading={launch.isPending}
                  onClick={() => launch.mutate(launch.variables)}
                >
                  {t("Retry authorization request")}
                </Button>
              )}
              {status.data && (
                <Button
                  variant="outline"
                  onClick={() => launch.mutate(status.data)}
                >
                  {t("Restart authorization")}
                </Button>
              )}
            </>
          )}
        </div>
      ) : launch.data ? (
        <>
          <StateBadge
            state={status.data?.status ?? launch.data.connection.status}
          />
          {status.data?.status !== "ready" && (
            <AuthorizationLink
              url={launch.data.redirect_url}
              expiresAt={launch.data.expires_at}
              sameTab={launch.data.completion_method !== "polling"}
            />
          )}
          {(!launch.data.redirect_url ||
            status.data?.status === "action_required" ||
            Date.now() >= Date.parse(launch.data.expires_at)) &&
            status.data?.status !== "ready" && (
              <Button
                loading={launch.isPending}
                onClick={() =>
                  launch.mutate(status.data ?? launch.data!.connection)
                }
              >
                {t("Restart authorization")}
              </Button>
            )}
          <Button
            variant="outline"
            onClick={() => void status.refetch()}
            type="button"
          >
            {t("Refresh connection")}
          </Button>
        </>
      ) : definition.isPending ? (
        <Loading />
      ) : (
        <form
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault();
            launch.mutate(undefined);
          }}
        >
          {!connection && (
            <FormField label={t("Connection name")}>
              <Input
                required
                value={name}
                disabled={started}
                maxLength={128}
                onChange={(event) => setName(event.target.value)}
              />
            </FormField>
          )}
          {definition.data && !definition.data.unavailable_reason && (
            <fieldset disabled={started}>
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
            className="justify-self-start"
            loading={definition.isFetching}
            disabled={started}
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
                label={t(current ? "Authorize connection" : "Connect")}
              />
            )
          )}
        </form>
      )}
    </div>
  );
}
