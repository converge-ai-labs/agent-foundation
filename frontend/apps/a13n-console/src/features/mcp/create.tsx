import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChoiceField, FormField, Input } from "a13n-ui";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { AuthorizationLink } from "../../shared/authorization-link";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions, TextAreaField } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";
import type { MCPPreset } from "../connections/presets";
import { saveMCPAuthorization } from "./authorization-context";
import { MCPCredentialFields } from "./credentials";

export function CreateMCP({
  preset,
  endpoint: initialEndpoint = "",
  onStarted,
  onSuccess,
}: {
  preset?: MCPPreset;
  endpoint?: string;
  onStarted: () => void;
  onSuccess: (value: Schema["MCPConnection"]) => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation(),
    key = useIdempotency();
  const created = useRef<Schema["MCPConnection"]>(undefined);
  const [name, setName] = useState(preset?.name ?? ""),
    [endpoint, setEndpoint] = useState(preset?.endpoint ?? initialEndpoint),
    [mode, setMode] = useState<Schema["MCPAuthMode"]>(preset?.auth ?? "oauth"),
    [names, setNames] = useState(""),
    [bearer, setBearer] = useState(""),
    [headers, setHeaders] = useState<Record<string, string>>({}),
    [started, setStarted] = useState(false),
    [launch, setLaunch] = useState<Schema["MCPAuthorizationLaunch"]>();
  const headerNames = Array.from(
    new Set(
      names
        .split("\n")
        .map((value) => value.trim())
        .filter(Boolean),
    ),
  );
  const connect = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      setStarted(true);
      onStarted();
      let connection = created.current;
      if (!connection) {
        const body = {
          name,
          endpoint_url: endpoint,
          auth_mode: mode,
          static_header_names: mode === "static_headers" ? headerNames : [],
        };
        connection = data(
          await client.http.POST(
            "/api/v1/workspaces/{workspace}/mcp-connections",
            {
              params: {
                path: { workspace: workspace.id },
                header: commandHeaders(workspace.id, key.forBody(body)),
              },
              body,
            },
          ),
        );
        created.current = connection;
        void cache.invalidateQueries({ queryKey: ["mcp-connections"] });
      }
      const path = { connection_id: connection.id };
      if (mode === "oauth") {
        const body = { expected_version: connection.version };
        const result = data(
          await client.http.POST(
            "/api/v1/mcp-connections/{connection_id}/authorize",
            {
              params: {
                path,
                header: commandHeaders(
                  workspace.id,
                  key.forBody({ authorize: connection.id, ...body }),
                ),
              },
              body,
            },
          ),
        );
        const href = saveMCPAuthorization(result, connection, basePath);
        setLaunch(result);
        window.location.assign(href);
        return;
      }
      if (mode !== "none" && !connection.credential_configured) {
        const body = {
          expected_version: connection.version,
          ...(mode === "bearer" ? { bearer } : { static_headers: headers }),
        };
        connection = data(
          await client.http.POST(
            "/api/v1/mcp-connections/{connection_id}/credentials",
            {
              params: {
                path,
                header: commandHeaders(workspace.id, key.forBody(body)),
              },
              body,
            },
          ),
        );
        created.current = connection;
        setBearer("");
        setHeaders({});
      }
      const body = { expected_version: connection.version };
      const result = data(
        await client.http.POST(
          "/api/v1/mcp-connections/{connection_id}/reconnect",
          {
            params: {
              path,
              header: commandHeaders(
                workspace.id,
                key.forBody({ reconnect: connection.id, ...body }),
              ),
            },
            body,
          },
        ),
      );
      onSuccess(result);
    },
    onSettled: () => {
      void cache.invalidateQueries({ queryKey: ["mcp-connections"] });
    },
  });
  return launch ? (
    <AuthorizationLink
      url={launch.authorization_url}
      expiresAt={launch.expires_at}
      sameTab
    />
  ) : (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        connect.mutate();
      }}
    >
      {preset?.notice && (
        <p role="status" className="text-sm text-muted-foreground">
          {t(preset.notice)}
        </p>
      )}
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
      {mode === "static_headers" && !started && (
        <TextAreaField
          label={t("Header names")}
          hint={t("One name per line.")}
          value={names}
          onChange={setNames}
          required
          rows={3}
        />
      )}
      {!created.current?.credential_configured && (
        <MCPCredentialFields
          mode={mode}
          names={headerNames}
          bearer={bearer}
          onBearer={setBearer}
          headers={headers}
          onHeaders={setHeaders}
        />
      )}
      {preset && (
        <a href={preset.docs} target="_blank" rel="noopener noreferrer">
          {t("Setup guide")}
        </a>
      )}
      <ErrorNotice error={connect.error} />
      <FormActions
        pending={connect.isPending}
        label={t(created.current ? "Continue connection" : "Connect")}
      />
    </form>
  );
}
