import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChoiceField, FormField, Input } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions, TextAreaField } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";

export function CreateMCP({
  onSuccess,
}: {
  onSuccess: (value: Schema["MCPConnection"]) => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation(),
    key = useIdempotency();
  const [name, setName] = useState(""),
    [endpoint, setEndpoint] = useState(""),
    [mode, setMode] = useState<Schema["MCPAuthMode"]>("oauth"),
    [headers, setHeaders] = useState("");
  const create = useMutation({
    mutationFn: () => {
      const body = {
        name,
        endpoint_url: endpoint,
        auth_mode: mode,
        static_header_names:
          mode === "static_headers"
            ? headers
                .split("\n")
                .map((value) => value.trim())
                .filter(Boolean)
            : [],
      };
      return client.http
        .POST("/api/v1/workspaces/{workspace}/mcp-connections", {
          params: {
            path: { workspace: workspace.id },
            header: commandHeaders(workspace.id, key.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: (result) => {
      void cache.invalidateQueries({ queryKey: ["mcp-connections"] });
      onSuccess(result);
    },
  });
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate();
      }}
    >
      <FormField className="min-w-0 w-full" label={t("Name")}>
        <Input
          required={true}
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={128}
        />
      </FormField>
      <FormField className="min-w-0 w-full" label={t("MCP endpoint URL")}>
        <Input
          required={true}
          type="url"
          value={endpoint}
          onChange={(event) => setEndpoint(event.target.value)}
          placeholder="https://example.com/mcp"
        />
      </FormField>
      <ChoiceField
        placeholder={t("Select authentication")}
        value={mode}
        className="min-w-0"
        onValueChange={(value) => {
          if (
            value === "none" ||
            value === "oauth" ||
            value === "bearer" ||
            value === "static_headers"
          )
            setMode(value);
        }}
        label={t("Authentication")}
        options={(["oauth", "bearer", "static_headers", "none"] as const).map(
          (value) => ({ value, label: t(`auth.${value}`) }),
        )}
      />
      {mode === "static_headers" && (
        <TextAreaField
          label={t("Header names")}
          hint={t(
            "One name per line. Supply secret values after creating the connection.",
          )}
          value={headers}
          onChange={setHeaders}
          required
          rows={3}
        />
      )}
      <ErrorNotice error={create.error} />
      <FormActions pending={create.isPending} label={t("Create connection")} />
    </form>
  );
}
