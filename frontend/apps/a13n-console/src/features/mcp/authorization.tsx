import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { MCPCredentialFields } from "./credentials";
import { MCPOAuthSetup } from "./oauth-setup";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, ifMatch, rowTag, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import { requireTestSuccess, testConnection } from "../connections/api";
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
    { t } = useTranslation(),
    basis = initial,
    [bearer, setBearer] = useState(""),
    [headers, setHeaders] = useState<Record<string, string>>({});
  const mode = basis.auth;
  const credentials = useMutation({
    gcTime: 0,
    mutationFn: () =>
      client
        .workspace(basis.workspace_id)
        .PATCH("/api/v1/connections/{connection_id}", {
          params: { path: { connection_id: basis.id } },
          headers: ifMatch(rowTag(basis)),
          body: {
            credential: mode === "bearer" ? { token: bearer } : { headers },
          },
        })
        .then(data),
    onSuccess: () => {
      setBearer("");
      setHeaders({});
      void cache.invalidateQueries({ queryKey: ["connections"] });
      void reload();
    },
  });
  const reconnect = useMutation({
    mutationFn: () => testConnection(client, basis).then(requireTestSuccess),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["connections"] });
      void reload();
    },
  });
  return (
    <div className={mcp.authorization}>
      <div>
        <h3 className={mcp.authorizationTitle}>{t(`auth.${mode}`)}</h3>
        <p className={mcp.hint}>
          {t(
            mode === "none"
              ? "This server does not require credentials. Verify the connection to refresh its available tools."
              : "Manage the credentials used to access this server.",
          )}
        </p>
      </div>
      {mode === "oauth" ? (
        <MCPOAuthSetup
          connection={basis}
          onConnectionChange={onConnectionChange}
        />
      ) : (
        mode !== "none" && (
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
              mode={mode}
              names={"url" in basis.config ? (basis.config.headers ?? []) : []}
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
      {mode !== "oauth" && (
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
