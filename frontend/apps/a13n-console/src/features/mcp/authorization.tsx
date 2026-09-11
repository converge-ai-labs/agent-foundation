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
import { FormActions } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";

export function MCPAuthorization({
  initial,
  reload,
  onConnectionChange,
}: {
  initial: Schema["MCPConnection"];
  reload: () => Promise<void>;
  onConnectionChange: (connection: Schema["MCPConnection"]) => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation(),
    key = useIdempotency(),
    basis = initial,
    [bearer, setBearer] = useState(""),
    [headers, setHeaders] = useState<Record<string, string>>({});
  const credentials = useMutation({
    gcTime: 0,
    mutationFn: () => {
      const body = {
        expected_version: basis.version,
        ...(basis.auth_mode === "bearer"
          ? { bearer }
          : { static_headers: headers }),
      };
      return client.http
        .POST("/api/v1/mcp-connections/{connection_id}/credentials", {
          params: {
            path: { connection_id: basis.id },
            header: commandHeaders(workspace.id, key.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: () => {
      setBearer("");
      setHeaders({});
      void cache.invalidateQueries({ queryKey: ["mcp-connections"] });
      void reload();
    },
  });
  const reconnect = useMutation({
    mutationFn: () => {
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
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["mcp-connections"] });
      void reload();
    },
  });
  return (
    <div className="grid justify-items-start gap-4">
      <div className="grid gap-1">
        <h3 className="text-sm font-medium">{t(`auth.${basis.auth_mode}`)}</h3>
        <p className="text-sm text-muted-foreground">
          {t(
            basis.auth_mode === "none"
              ? "This server does not require credentials. Verify the connection to refresh its available tools."
              : "Manage the credentials used to access this server.",
          )}
        </p>
      </div>
      {basis.auth_mode === "oauth" ? (
        <>
          <MCPOAuthSetup
            connection={basis}
            onConnectionChange={onConnectionChange}
          />
          <Button variant="outline" onClick={() => void reload()} type="button">
            {t("Refresh connection")}
          </Button>
        </>
      ) : (
        basis.auth_mode !== "none" && (
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
              mode={basis.auth_mode}
              names={basis.static_header_names}
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
      <Button
        variant="outline"
        loading={reconnect.isPending}
        onClick={() => reconnect.mutate()}
        type="button"
      >
        {t("Verify connection")}
      </Button>
      <ErrorNotice
        error={credentials.error ?? reconnect.error}
        retry={() => void reload()}
      />
    </div>
  );
}
