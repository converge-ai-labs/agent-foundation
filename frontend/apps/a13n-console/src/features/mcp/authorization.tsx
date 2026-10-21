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
  const names = "url" in basis.config ? (basis.config.headers ?? []) : [];
  const complete =
    mode === "bearer"
      ? !!bearer.trim()
      : names.every((name) => !!headers[name]);
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
  const check = (
    <Button
      variant="outline"
      loading={reconnect.isPending}
      onClick={() => reconnect.mutate()}
      type="button"
    >
      {t(basis.status === "pending" ? "Retry check" : "Check connection")}
    </Button>
  );
  return (
    <div className={mcp.authorization}>
      <div>
        <h3 className={mcp.authorizationTitle}>{t(`auth.${mode}`)}</h3>
        <p className={mcp.hint}>
          {t(
            mode === "none"
              ? "This server does not require credentials. Verify the connection to refresh its available tools."
              : mode === "oauth"
                ? "Manage the credentials used to access this server."
                : "Existing credentials are never displayed. Supply a complete replacement.",
          )}
        </p>
      </div>
      {mode === "oauth" ? (
        <MCPOAuthSetup
          connection={basis}
          onConnectionChange={onConnectionChange}
        />
      ) : mode === "none" ? (
        <div className={mcp.authorizationActions}>{check}</div>
      ) : (
        <form
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault();
            credentials.mutate();
          }}
        >
          <MCPCredentialFields
            mode={mode}
            replace
            names={names}
            bearer={bearer}
            onBearer={setBearer}
            headers={headers}
            onHeaders={setHeaders}
          />
          <FormActions
            pending={credentials.isPending}
            disabled={!complete}
            leading={check}
            label={t("Save credentials")}
          />
        </form>
      )}
      <ErrorNotice
        error={credentials.error ?? reconnect.error}
        retry={() => void reload()}
      />
    </div>
  );
}
