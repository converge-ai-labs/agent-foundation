import { requireCompletedAuthorization } from "../connections/authorization-context";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChoiceField, FormField, Input } from "a13n-ui";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";
import type { MCPPreset } from "../connections/presets";
import { MCPOAuthSetup } from "./oauth-setup";
import {
  HeaderFields,
  serializeHeaders,
  type HeaderDraft,
} from "../../shared/header-fields";
import { MCPCredentialFields } from "./credentials";

export function CreateMCP({
  preset,
  endpoint: initialEndpoint = "",
  onStarted,
  onSuccess,
  onCancel,
}: {
  preset?: MCPPreset;
  endpoint?: string;
  onStarted: () => void;
  onSuccess: (value: Schema["Connection"]) => void;
  onCancel: () => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation(),
    key = useIdempotency();
  const created = useRef<Schema["Connection"]>(undefined);
  const [name, setName] = useState(preset?.name ?? ""),
    [endpoint, setEndpoint] = useState(preset?.endpoint ?? initialEndpoint),
    [mode, setMode] = useState<Schema["MCPSource"]["auth_mode"]>(
      preset?.auth ?? "oauth",
    ),
    [headerRows, setHeaderRows] = useState<HeaderDraft[]>(() =>
      (preset?.headerNames ?? [""]).map((name) => ({
        id: crypto.randomUUID(),
        name,
        value: "",
      })),
    ),
    [bearer, setBearer] = useState(""),
    [started, setStarted] = useState(false),
    [oauthConnection, setOAuthConnection] = useState<Schema["Connection"]>();
  const connect = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const headers = Object.fromEntries(
        Object.entries(
          mode === "static_headers" && !created.current?.credential_configured
            ? serializeHeaders(headerRows, [])
            : {},
        ).filter((entry): entry is [string, string] => entry[1] !== null),
      );
      const headerNames = headerRows.map((row) =>
        row.name.trim().toLowerCase(),
      );
      if (mode === "static_headers" && !headerNames.length)
        throw new Error(t("Add at least one header."));
      setStarted(true);
      onStarted();
      let connection = created.current;
      if (!connection) {
        const body: Schema["CreateConnectionRequest"] = {
          name,
          source: {
            kind: "mcp",
            endpoint_url: endpoint,
            auth_mode: mode,
            static_header_names: mode === "static_headers" ? headerNames : [],
          },
        };
        connection = data(
          await client.http.POST("/api/v1/workspaces/{workspace}/connections", {
            params: {
              path: { workspace: workspace.id },
              header: commandHeaders(workspace.id, key.forBody(body)),
            },
            body,
          }),
        );
        created.current = connection;
        void cache.invalidateQueries({ queryKey: ["connections"] });
      }
      const path = { connection_id: connection.id };
      if (mode === "oauth") {
        setOAuthConnection(connection);
        return;
      }
      if (mode !== "none" && !connection.credential_configured) {
        const body = {
          expected_version: connection.version,
          method: "credentials" as const,
          credentials: mode === "bearer" ? { bearer } : headers,
        };
        requireCompletedAuthorization(
          data(
            await client.http.POST(
              "/api/v1/connections/{connection_id}/authorizations",
              {
                params: {
                  path,
                  header: commandHeaders(workspace.id, key.forBody(body)),
                },
                body,
              },
            ),
          ),
        );
        connection = data(
          await client.http.GET("/api/v1/connections/{connection_id}", {
            params: { path },
          }),
        );
        created.current = connection;
        setBearer("");
        setHeaderRows((rows) =>
          rows.map((row) => ({
            ...row,
            value: "",
            savedName: row.name.trim().toLowerCase(),
          })),
        );
      }
      const body = { expected_version: connection.version };
      const result = data(
        await client.http.POST("/api/v1/connections/{connection_id}/check", {
          params: {
            path,
          },
          body,
        }),
      );
      onSuccess(result);
    },
    onSettled: () => {
      void cache.invalidateQueries({ queryKey: ["connections"] });
    },
  });
  return oauthConnection ? (
    <div className={styles.stack}>
      {preset && <p className={styles.muted}>{t(preset.requirements)}</p>}
      {preset && (
        <a href={preset.docs} target="_blank" rel="noopener noreferrer">
          {t("Setup guide")}
        </a>
      )}
      <MCPOAuthSetup
        connection={oauthConnection}
        onConnectionChange={setOAuthConnection}
        autoStart
      />
    </div>
  ) : (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        connect.mutate();
      }}
    >
      <FormField label={t("Connection name")}>
        <Input
          required
          value={name}
          disabled={started}
          maxLength={128}
          onChange={(event) => setName(event.target.value)}
        />
      </FormField>
      <FormField label={t("MCP endpoint URL")}>
        <Input
          required
          type="url"
          value={endpoint}
          disabled={started}
          onChange={(event) => setEndpoint(event.target.value)}
          placeholder="https://example.com/mcp"
        />
      </FormField>
      <ChoiceField
        placeholder={t("Select authentication")}
        label={t("Authentication")}
        value={mode}
        disabled={started}
        onValueChange={(value) => {
          if (
            value === "none" ||
            value === "oauth" ||
            value === "bearer" ||
            value === "static_headers"
          )
            setMode(value);
        }}
        options={(["oauth", "bearer", "static_headers", "none"] as const).map(
          (value) => ({ value, label: t(`auth.${value}`) }),
        )}
      />
      {!created.current?.credential_configured &&
        (mode === "static_headers" ? (
          <HeaderFields
            rows={headerRows}
            onChange={setHeaderRows}
            disabled={started}
            maxRows={16}
          />
        ) : (
          <MCPCredentialFields
            mode={mode}
            names={[]}
            bearer={bearer}
            onBearer={setBearer}
            headers={{}}
            onHeaders={() => {}}
          />
        ))}
      {preset && <p className={styles.muted}>{t(preset.requirements)}</p>}
      {preset && (
        <a href={preset.docs} target="_blank" rel="noopener noreferrer">
          {t("Setup guide")}
        </a>
      )}
      <ErrorNotice error={connect.error} />
      <FormActions
        onCancel={onCancel}
        pending={connect.isPending}
        label={t(created.current ? "Continue connection" : "Connect")}
      />
    </form>
  );
}
