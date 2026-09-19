import { requireCompletedAuthorization } from "../connections/authorization-context";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { MCPCredentialFields } from "./credentials";
import { MCPOAuthSetup } from "./oauth-setup";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";
import mcp from "./mcp.module.css";

export function MCPAuthorization({
  initial,
  reload,
  onConnectionChange,
}: {
  initial: Schema["Connection"];
  reload: () => Promise<void>;
  onConnectionChange: (connection: Schema["Connection"]) => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation(),
    key = useIdempotency(),
    basis = initial,
    [bearer, setBearer] = useState(""),
    [headers, setHeaders] = useState<Record<string, string>>({});
  const source = basis.source;
  const credentials = useMutation({
    gcTime: 0,
    mutationFn: () => {
      const body = {
        expected_version: basis.version,
        method: "credentials" as const,
        credentials:
          source.kind === "mcp" && source.auth_mode === "bearer"
            ? { bearer }
            : headers,
      };
      return client.http
        .POST("/api/v1/connections/{connection_id}/authorizations", {
          params: {
            path: { connection_id: basis.id },
            header: commandHeaders(workspace.id, key.forBody(body)),
          },
          body,
        })
        .then(data)
        .then(requireCompletedAuthorization);
    },
    onSuccess: () => {
      setBearer("");
      setHeaders({});
      void cache.invalidateQueries({ queryKey: ["connections"] });
      void reload();
    },
  });
  const reconnect = useMutation({
    mutationFn: () => {
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
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["connections"] });
      void reload();
    },
  });
  return (
    <div className={mcp.authorization}>
      <div>
        <h3 className={mcp.authorizationTitle}>
          {t(`auth.${source.kind === "mcp" ? source.auth_mode : "none"}`)}
        </h3>
        <p className={mcp.hint}>
          {t(
            (source.kind === "mcp" ? source.auth_mode : "none") === "none"
              ? "This server does not require credentials. Verify the connection to refresh its available tools."
              : "Manage the credentials used to access this server.",
          )}
        </p>
      </div>
      {(source.kind === "mcp" ? source.auth_mode : "none") === "oauth" ? (
        <MCPOAuthSetup
          connection={basis}
          onConnectionChange={onConnectionChange}
        />
      ) : (
        (source.kind === "mcp" ? source.auth_mode : "none") !== "none" && (
          <form
            className={styles.form}
            onSubmit={(event) => {
              event.preventDefault();
              credentials.mutate();
            }}
          >
            <h3>{t("Replace credentials")}</h3>
            <p className={styles.muted}>
              {t(
                "Existing credentials are never displayed. Supply a complete replacement.",
              )}
            </p>
            <MCPCredentialFields
              mode={source.kind === "mcp" ? source.auth_mode : "none"}
              names={
                source.kind === "mcp" ? (source.static_header_names ?? []) : []
              }
              bearer={bearer}
              onBearer={setBearer}
              headers={headers}
              onHeaders={setHeaders}
            />
            <FormActions
              pending={credentials.isPending}
              label={t("Save credentials")}
            />
          </form>
        )
      )}
      {(source.kind === "mcp" ? source.auth_mode : "none") !== "oauth" && (
        <Button
          variant="outline"
          loading={reconnect.isPending}
          onClick={() => reconnect.mutate()}
          type="button"
        >
          {t(basis.status === "pending" ? "Retry check" : "Check connection")}
        </Button>
      )}
      <ErrorNotice
        error={credentials.error ?? reconnect.error}
        retry={() => void reload()}
      />
    </div>
  );
}
