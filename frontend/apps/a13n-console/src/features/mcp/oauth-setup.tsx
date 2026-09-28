import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, DisclosureSection } from "a13n-ui";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ApiError } from "../../service-client";
import { data, type Schema } from "../../shared/api";
import { AuthorizationLink } from "../../shared/authorization-link";
import { ErrorNotice, StatePill } from "../../shared/feedback";
import styles from "./mcp.module.css";
import { connectionState } from "../connections/api";
import {
  authorizeConnection,
  startBrowserAuthorization,
} from "../connections/authorization-context";
import { MCPOAuthClientEditor } from "./oauth-client";

type GrantType = Schema["OAuthGrant"];

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
    { basePath } = useWorkspace(),
    { t } = useTranslation(),
    started = useRef(false),
    [editing, setEditing] = useState(false);
  const config = "url" in connection.config ? connection.config : null;
  const grant = config?.oauth?.grant_type ?? "authorization_code";
  const authorize = useMutation({
    gcTime: 0,
    mutationFn: (basis: Schema["Connection"]) =>
      startBrowserAuthorization(client, basis, basePath),
  });
  // A machine account obtains its token without a browser.
  const authenticate = useMutation({
    gcTime: 0,
    mutationFn: async (basis: Schema["Connection"]) => {
      await authorizeConnection(client, basis);
      return data(
        await client
          .workspace(basis.workspace_id)
          .GET("/api/v1/connections/{connection_id}", {
            params: { path: { connection_id: basis.id } },
          }),
      );
    },
    onSuccess: (updated) => {
      onConnectionChange(updated);
      void cache.invalidateQueries({ queryKey: ["connections"] });
    },
  });
  const connect = (basis: Schema["Connection"], using: GrantType = grant) => {
    if (using === "client_credentials") {
      authorize.reset();
      authenticate.mutate(basis);
    } else {
      authenticate.reset();
      authorize.mutate(basis);
    }
  };
  const error = authorize.error ?? authenticate.error;
  // Without dynamic registration, or without the client's secret, the server needs your own client details.
  const reason = error instanceof ApiError ? error.details.reason : undefined;
  const needsClientSetup =
    reason === "client_registration_failed" ||
    reason === "client_secret_missing";
  useEffect(() => {
    if (started.current || !autoStart) return;
    started.current = true;
    connect(connection);
  }, [autoStart, connection]);
  return (
    <div className={styles.authorization}>
      {authorize.data ? (
        <AuthorizationLink
          sameTab
          url={authorize.data.redirect_url}
          expiresAt={authorize.data.expires_at}
        />
      ) : (
        <>
          <div className={styles.authorizationState}>
            <StatePill state={connectionState(connection)} />
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
              loading={authorize.isPending || authenticate.isPending}
              disabled={!connection.enabled}
              onClick={() => connect(connection)}
            >
              {t(
                connection.status === "ready" ||
                  connection.status === "reauthorization_required"
                  ? "Reauthorize"
                  : grant === "client_credentials"
                    ? "Connect"
                    : "Continue authorization",
              )}
            </Button>
          </div>
          {config && (
            <DisclosureSection
              title={t("Use your own OAuth app")}
              open={editing || needsClientSetup}
              onOpenChange={(value) => setEditing(value)}
            >
              <MCPOAuthClientEditor
                connection={connection}
                config={config}
                onCancel={
                  needsClientSetup ? undefined : () => setEditing(false)
                }
                onSaved={(updated, saved) => {
                  onConnectionChange(updated);
                  setEditing(false);
                  connect(updated, saved);
                }}
              />
            </DisclosureSection>
          )}
        </>
      )}
      <ErrorNotice error={error} />
    </div>
  );
}
