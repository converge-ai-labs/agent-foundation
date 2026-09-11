import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { AuthorizationLink } from "../../shared/authorization-link";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";
import { saveMCPAuthorization } from "./authorization-context";
import { MCPOAuthClientEditor } from "./oauth-client";

export function MCPOAuthSetup({
  connection,
  onConnectionChange,
  autoStart = false,
}: {
  connection: Schema["MCPConnection"];
  onConnectionChange: (connection: Schema["MCPConnection"]) => void;
  autoStart?: boolean;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation(),
    key = useIdempotency(),
    started = useRef(false),
    [editing, setEditing] = useState(false);
  const needsVerification =
    connection.status === "pending" && connection.credential_configured;
  const setup = useQuery({
    queryKey: ["mcp-oauth-setup", connection.id, connection.version],
    enabled: !needsVerification,
    queryFn: async ({ signal }) => {
      const params = { path: { connection_id: connection.id } };
      const [configuration, discovery] = await Promise.all([
        client.http
          .GET("/api/v1/mcp-connections/{connection_id}/oauth-client", {
            params,
            signal,
          })
          .then(data),
        client.http
          .POST("/api/v1/mcp-connections/{connection_id}/oauth-discovery", {
            params,
            signal,
          })
          .then(data),
      ]);
      return { configuration, discovery };
    },
  });
  const authorize = useMutation({
    gcTime: 0,
    mutationFn: (basis: Schema["MCPConnection"]) => {
      const body = { expected_version: basis.version };
      return client.http
        .POST("/api/v1/mcp-connections/{connection_id}/authorize", {
          params: {
            path: { connection_id: basis.id },
            header: commandHeaders(
              workspace.id,
              key.forBody({ authorize: basis.id, ...body }),
            ),
          },
          body,
        })
        .then(data);
    },
    onSuccess: (launch, basis) => {
      const href = saveMCPAuthorization(launch, basis, basePath);
      void cache.invalidateQueries({ queryKey: ["mcp-connections"] });
      window.location.assign(href);
    },
  });
  const authenticate = useMutation({
    gcTime: 0,
    mutationFn: (basis: Schema["MCPConnection"]) => {
      const body = { expected_version: basis.version };
      return client.http
        .POST("/api/v1/mcp-connections/{connection_id}/authenticate", {
          params: {
            path: { connection_id: basis.id },
            header: commandHeaders(
              workspace.id,
              key.forBody({ authenticate: basis.id, ...body }),
            ),
          },
          body,
        })
        .then(data);
    },
    onSuccess: onConnectionChange,
  });
  const verify = useMutation({
    mutationFn: (basis: Schema["MCPConnection"]) => {
      const body = { expected_version: basis.version };
      return client.http
        .POST("/api/v1/mcp-connections/{connection_id}/reconnect", {
          params: {
            path: { connection_id: basis.id },
            header: commandHeaders(
              workspace.id,
              key.forBody({ reconnect: basis.id, ...body }),
            ),
          },
          body,
        })
        .then(data);
    },
    onSuccess: onConnectionChange,
  });
  const configuration = setup.data?.configuration;
  const needsClientSetup =
    configuration === null &&
    (setup.data?.discovery.client_registration === "manual" ||
      !setup.data?.discovery.grant_types_supported.includes(
        "authorization_code",
      ));
  const connect = (basis: Schema["MCPConnection"]) => {
    if (configuration?.grant_type === "client_credentials")
      authenticate.mutate(basis);
    else authorize.mutate(basis);
  };
  useEffect(() => {
    if (needsVerification) return;
    if (started.current || !autoStart || !setup.data) return;
    started.current = true;
    if (connection.credential_configured) verify.mutate(connection);
    else if (!needsClientSetup) connect(connection);
  }, [autoStart, connection, needsVerification, setup.data]);
  if (needsVerification)
    return (
      <div className={styles.stack}>
        <Button
          type="button"
          loading={verify.isPending}
          disabled={connection.status === "disabled"}
          onClick={() => verify.mutate(connection)}
        >
          {t("Retry verification")}
        </Button>
        <ErrorNotice error={verify.error} />
      </div>
    );
  if (setup.isPending) return <Loading />;
  if (setup.error)
    return (
      <ErrorNotice error={setup.error} retry={() => void setup.refetch()} />
    );
  if (editing || needsClientSetup)
    return (
      <MCPOAuthClientEditor
        connection={connection}
        configuration={setup.data.configuration}
        discovery={setup.data.discovery}
        onCancel={needsClientSetup ? undefined : () => setEditing(false)}
        onSaved={(updated, grant) => {
          onConnectionChange(updated);
          setEditing(false);
          if (grant === "client_credentials") authenticate.mutate(updated);
          else authorize.mutate(updated);
        }}
      />
    );
  const pending =
    authorize.isPending || authenticate.isPending || verify.isPending;
  const error = authorize.error ?? authenticate.error ?? verify.error;
  return (
    <div className={styles.stack}>
      {authorize.data ? (
        <AuthorizationLink
          sameTab
          url={authorize.data.authorization_url}
          expiresAt={authorize.data.expires_at}
        />
      ) : connection.status === "ready" ? (
        <p className={styles.muted}>
          {t("This connection is authorized and verified.")}
        </p>
      ) : (
        <Button
          type="button"
          loading={pending}
          disabled={connection.status === "disabled"}
          onClick={() =>
            needsVerification ? verify.mutate(connection) : connect(connection)
          }
        >
          {t(
            needsVerification
              ? "Retry verification"
              : connection.status === "action_required"
                ? "Reauthorize"
                : configuration?.grant_type === "client_credentials"
                  ? "Connect"
                  : "Continue authorization",
          )}
        </Button>
      )}
      {!authorize.data && !needsVerification && (
        <Button
          type="button"
          variant="ghost"
          disabled={pending}
          onClick={() => setEditing(true)}
        >
          {t("Use your own OAuth app")}
        </Button>
      )}
      <ErrorNotice error={error} retry={() => void setup.refetch()} />
    </div>
  );
}
