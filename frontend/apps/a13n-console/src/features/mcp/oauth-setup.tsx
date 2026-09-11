import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { AuthorizationLink } from "../../shared/authorization-link";
import { ErrorNotice } from "../../shared/feedback";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";
import { saveMCPAuthorization } from "./authorization-context";
import { MCPOAuthClientEditor } from "./oauth-client";

export function MCPOAuthSetup({
  connection,
  onConnectionChange,
  autoStart = false,
  configureInitially = false,
}: {
  connection: Schema["MCPConnection"];
  onConnectionChange: (connection: Schema["MCPConnection"]) => void;
  autoStart?: boolean;
  configureInitially?: boolean;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation();
  const key = useIdempotency(),
    started = useRef(false);
  const [editing, setEditing] = useState(configureInitially);
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
    onError: async () => {
      const latest = await client.http
        .GET("/api/v1/mcp-connections/{connection_id}", {
          params: { path: { connection_id: connection.id } },
        })
        .then(data)
        .catch(() => undefined);
      if (latest) onConnectionChange(latest);
      void cache.invalidateQueries({ queryKey: ["mcp-connections"] });
    },
  });
  useEffect(() => {
    if (started.current || !autoStart || configureInitially) return;
    started.current = true;
    authorize.mutate(connection);
  }, [autoStart, configureInitially, connection, authorize.mutate]);
  if (editing)
    return (
      <MCPOAuthClientEditor
        connection={connection}
        onCancel={() => setEditing(false)}
        onSaved={(updated) => {
          onConnectionChange(updated);
          setEditing(false);
          authorize.mutate(updated);
        }}
      />
    );
  return (
    <div className={styles.stack}>
      {authorize.data ? (
        <AuthorizationLink
          sameTab
          url={authorize.data.authorization_url}
          expiresAt={authorize.data.expires_at}
        />
      ) : (
        <Button
          type="button"
          loading={authorize.isPending}
          disabled={connection.status === "disabled"}
          onClick={() => authorize.mutate(connection)}
        >
          {t("Authorize with OAuth")}
        </Button>
      )}
      <Button
        type="button"
        variant="outline"
        disabled={authorize.isPending}
        onClick={() => setEditing(true)}
      >
        {t("Configure your OAuth app")}
      </Button>
      <ErrorNotice error={authorize.error} />
    </div>
  );
}
