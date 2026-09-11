import { FormField, Input } from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";

export function MCPCredentialFields({
  mode,
  names,
  bearer,
  onBearer,
  headers,
  onHeaders,
}: {
  mode: Schema["MCPAuthMode"];
  names: readonly string[];
  bearer: string;
  onBearer: (value: string) => void;
  headers: Record<string, string>;
  onHeaders: (value: Record<string, string>) => void;
}) {
  const { t } = useTranslation();
  if (mode === "bearer")
    return (
      <FormField label={t("Bearer token")}>
        <Input
          required
          type="password"
          autoComplete="off"
          value={bearer}
          onChange={(event) => onBearer(event.target.value)}
        />
      </FormField>
    );
  if (mode !== "static_headers") return null;
  return names.map((name) => (
    <FormField label={name} key={name}>
      <Input
        required
        type="password"
        autoComplete="off"
        value={headers[name] ?? ""}
        onChange={(event) =>
          onHeaders({ ...headers, [name]: event.target.value })
        }
      />
    </FormField>
  ));
}
