import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChoiceField, FormField, Input, ReadOnlyField } from "a13n-ui";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import { ProviderKeyLink } from "../../shared/forms";
import styles from "../../shared/shared.module.css";
import { requireTestSuccess, testConnection } from "../connections/api";
import { MCPOAuthSetup } from "./oauth-setup";
import {
  HeaderFields,
  serializeHeaders,
  type HeaderDraft,
} from "../../shared/forms";
import { MCPCredentialFields } from "./credentials";

export function CreateMCP({
  preset,
  endpoint: initialEndpoint = "",
  onStarted,
  onSuccess,
  onCancel,
}: {
  preset?: Schema["McpServer"];
  endpoint?: string;
  onStarted: () => void;
  onSuccess: (value: Schema["Connection"]) => void;
  onCancel: () => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const created = useRef<Schema["Connection"]>(undefined);
  const [name, setName] = useState(preset?.name ?? ""),
    [endpoint, setEndpoint] = useState(preset?.url ?? initialEndpoint),
    [mode, setMode] = useState<Schema["McpAuth"]>(preset?.auth ?? "oauth"),
    [headerRows, setHeaderRows] = useState<HeaderDraft[]>(() =>
      (preset?.header_names ?? [""]).map((name) => ({
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
          mode === "headers" && !created.current?.credential_configured
            ? serializeHeaders(headerRows, [])
            : {},
        ).filter((entry): entry is [string, string] => entry[1] !== null),
      );
      const headerNames = headerRows.map((row) =>
        row.name.trim().toLowerCase(),
      );
      if (mode === "headers" && !headerNames.length)
        throw new Error(t("Add at least one header."));
      setStarted(true);
      onStarted();
      let connection = created.current;
      if (!connection) {
        // Entered credentials are written with the connection itself.
        const credential =
          mode === "bearer"
            ? { token: bearer }
            : mode === "headers"
              ? { headers }
              : undefined;
        connection = data(
          await client.workspace(workspace.id).POST("/api/v1/connections", {
            body: {
              type: "mcp",
              name,
              config: {
                url: endpoint,
                headers: mode === "headers" ? headerNames : [],
              },
              auth: mode,
              ...(credential ? { credential } : {}),
            },
          }),
        );
        created.current = connection;
        void cache.invalidateQueries({ queryKey: ["connections"] });
        setBearer("");
        setHeaderRows((rows) =>
          rows.map((row) => ({
            ...row,
            value: "",
            savedName: row.name.trim().toLowerCase(),
          })),
        );
      }
      if (mode === "oauth") {
        setOAuthConnection(connection);
        return;
      }
      requireTestSuccess(await testConnection(client, connection));
      onSuccess(connection);
    },
    onSettled: () => {
      void cache.invalidateQueries({ queryKey: ["connections"] });
    },
  });
  return oauthConnection ? (
    <div className={styles.stack}>
      {preset?.requirements && (
        <p className={styles.muted}>{t(preset.requirements)}</p>
      )}
      {preset?.documentation_url && (
        <ProviderKeyLink href={preset.documentation_url} label="Setup guide" />
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
      {preset ? (
        <ReadOnlyField
          label={t("Authentication")}
          description={
            preset.documentation_url ? (
              <ProviderKeyLink
                href={preset.documentation_url}
                label="Setup guide"
              />
            ) : undefined
          }
        >
          {t(`auth.${mode}`)}
        </ReadOnlyField>
      ) : (
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
              value === "headers"
            )
              setMode(value);
          }}
          options={(["oauth", "bearer", "headers", "none"] as const).map(
            (value) => ({ value, label: t(`auth.${value}`) }),
          )}
        />
      )}
      {!created.current?.credential_configured &&
        (mode === "headers" ? (
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
      {preset?.requirements && (
        <p className={styles.muted}>{t(preset.requirements)}</p>
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
