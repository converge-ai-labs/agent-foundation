import { requireCompletedAuthorization } from "../connections/authorization-context";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, DisclosureSection } from "a13n-ui";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { AuthorizationLink } from "../../shared/authorization-link";
import { ErrorNotice, Loading, StatePill } from "../../shared/feedback";
import { useIdempotency } from "../../shared/idempotency";
import styles from "./mcp.module.css";
import { startBrowserAuthorization } from "../connections/authorization-context";
import { MCPOAuthClientEditor } from "./oauth-client";

export function MCPOAuthSetup({
  connection,
  onConnectionChange,
  autoStart = false,
}: {
  connection: Schema["Connection"];
  onConnectionChange: (connection: Schema["Connection"]) => void;
  autoStart?: boolean;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation(),
    key = useIdempotency(),
    started = useRef(false),
    [editing, setEditing] = useState(false),
    redirectUri = `${window.location.origin}/connections/callback`;
  const needsVerification =
    connection.status === "pending" && connection.credential_configured;
  const setup = useQuery({
    queryKey: ["mcp-oauth-setup", connection.id, connection.version],
    enabled: !needsVerification,
    queryFn: async ({ signal }) => {
      return client.http
        .POST("/api/v1/connections/{connection_id}/mcp/oauth-setup", {
          params: { path: { connection_id: connection.id } },
          body: {
            redirect_uri: redirectUri,
          },
          signal,
        })
        .then(data);
    },
  });
  const authorize = useMutation({
    gcTime: 0,
    mutationFn: (basis: Schema["Connection"]) =>
      startBrowserAuthorization(client, basis, basePath),
  });
  const authenticate = useMutation({
    gcTime: 0,
    mutationFn: (basis: Schema["Connection"]) => {
      const body = {
        expected_version: basis.version,
        method: "client_credentials" as const,
      };
      return client.http
        .POST("/api/v1/connections/{connection_id}/authorizations", {
          params: {
            path: { connection_id: basis.id },
            header: commandHeaders(
              workspace.id,
              key.forBody({ authenticate: basis.id, ...body }),
            ),
          },
          body,
        })
        .then(data)
        .then(requireCompletedAuthorization);
    },
    onSuccess: async () => {
      const updated = data(
        await client.http.GET("/api/v1/connections/{connection_id}", {
          params: { path: { connection_id: connection.id } },
        }),
      );
      onConnectionChange(updated);
      void cache.invalidateQueries({ queryKey: ["connections"] });
    },
  });
  const verify = useMutation({
    mutationFn: (basis: Schema["Connection"]) => {
      const body = { expected_version: basis.version };
      return client.http
        .POST("/api/v1/connections/{connection_id}/check", {
          params: {
            path: { connection_id: basis.id },
          },
          body,
        })
        .then(data);
    },
    onSuccess: onConnectionChange,
  });
  const configuration = setup.data?.client;
  const action = setup.data?.next_action;
  const needsClientSetup = action?.type === "configure_oauth_client";
  const discovery = useQuery({
    queryKey: ["mcp-oauth-discovery", connection.id, redirectUri],
    enabled: editing || needsClientSetup,
    queryFn: async ({ signal }) =>
      client.http
        .POST("/api/v1/connections/{connection_id}/mcp/oauth-discovery", {
          params: { path: { connection_id: connection.id } },
          body: { redirect_uri: redirectUri },
          signal,
        })
        .then(data),
  });
  const connect = (basis: Schema["Connection"]) => {
    if (action?.type === "authenticate_client_credentials")
      authenticate.mutate(basis);
    else authorize.mutate(basis);
  };
  useEffect(() => {
    if (needsVerification) return;
    if (started.current || !autoStart || !setup.data) return;
    started.current = true;
    if (connection.credential_configured) verify.mutate(connection);
    else if (action?.type === "check_connection") verify.mutate(connection);
    else if (
      action?.type === "start_authorization" ||
      action?.type === "authenticate_client_credentials"
    )
      connect(connection);
  }, [autoStart, connection, needsVerification, setup.data]);
  if (needsVerification)
    return (
      <div className={styles.authorization}>
        <div className={styles.authorizationState}>
          <StatePill state={connection.status} />
          <span>{t("Authorization is configured but not yet verified.")}</span>
        </div>
        <Button
          type="button"
          loading={verify.isPending}
          disabled={connection.status === "disabled"}
          onClick={() => verify.mutate(connection)}
        >
          {t("Check connection")}
        </Button>
        <ErrorNotice error={verify.error} />
      </div>
    );
  if (setup.isPending) return <Loading variant="list" rows={2} />;
  if (setup.error)
    return (
      <ErrorNotice error={setup.error} retry={() => void setup.refetch()} />
    );
  const pending =
    authorize.isPending || authenticate.isPending || verify.isPending;
  const error = authorize.error ?? authenticate.error ?? verify.error;
  const clientEditor = discovery.isPending ? (
    <Loading variant="list" rows={2} />
  ) : discovery.error ? (
    <ErrorNotice
      error={discovery.error}
      retry={() => void discovery.refetch()}
    />
  ) : (
    discovery.data && (
      <MCPOAuthClientEditor
        connection={connection}
        configuration={setup.data.client ?? null}
        discovery={discovery.data}
        onCancel={needsClientSetup ? undefined : () => setEditing(false)}
        onSaved={(updated, grant) => {
          onConnectionChange(updated);
          setEditing(false);
          if (grant === "client_credentials") authenticate.mutate(updated);
          else authorize.mutate(updated);
        }}
      />
    )
  );
  return (
    <div className={styles.authorization}>
      {authorize.data ? (
        <AuthorizationLink
          sameTab
          url={authorize.data.next_action?.url}
          expiresAt={authorize.data.expires_at}
        />
      ) : (
        <>
          <div className={styles.authorizationState}>
            <StatePill state={connection.status} />
            <span>
              {t(
                connection.status === "ready"
                  ? "This connection is authorized and verified."
                  : needsClientSetup
                    ? "This server needs your own OAuth application."
                    : "Authorize this server to let agents call its tools.",
              )}
            </span>
          </div>
          <div className={styles.authorizationActions}>
            <Button
              type="button"
              variant={connection.status === "ready" ? "outline" : "default"}
              loading={pending}
              disabled={connection.status === "disabled"}
              onClick={() => connect(connection)}
            >
              {t(
                connection.status === "ready" ||
                  connection.status === "action_required"
                  ? "Reauthorize"
                  : action?.type === "authenticate_client_credentials"
                    ? "Connect"
                    : "Continue authorization",
              )}
            </Button>
          </div>
          <DisclosureSection
            title={t("Use your own OAuth app")}
            open={editing || needsClientSetup}
            onOpenChange={(value) => setEditing(value)}
          >
            {clientEditor}
          </DisclosureSection>
        </>
      )}
      <ErrorNotice error={error} retry={() => void setup.refetch()} />
    </div>
  );
}
