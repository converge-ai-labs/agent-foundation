import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
} from "a13n-ui";
import { useTranslation } from "react-i18next";
import { SchemaFields } from "../../shared/schema-fields";
import { ProviderHeaders, type HeaderDraft } from "./provider-headers";

const advancedFields = new Set([
  "base_url",
  "auth_mode",
  "api_key_header_name",
  "mantle_base_url",
  "session_affinity_header",
]);

type AffinityPreset = { label: string; header: string; description: string };

function affinityPresets(property: unknown): AffinityPreset[] {
  if (!property || typeof property !== "object") return [];
  const field = property as Record<string, unknown>;
  const presets = field["x-session-affinity-presets"];
  if (Array.isArray(presets))
    return presets.filter(
      (p): p is AffinityPreset =>
        !!p &&
        typeof p.label === "string" &&
        typeof p.header === "string" &&
        typeof p.description === "string",
    );
  return Array.isArray(field.anyOf) ? field.anyOf.flatMap(affinityPresets) : [];
}

export function ordinaryConfigurationSchema(schema: Record<string, unknown>) {
  const properties = (schema.properties ?? {}) as Record<string, unknown>;
  return {
    ...schema,
    properties: Object.fromEntries(
      Object.entries(properties).filter(
        ([key]) =>
          !advancedFields.has(key) ||
          (key === "base_url" &&
            Array.isArray(schema.required) &&
            schema.required.includes(key)),
      ),
    ),
  };
}

export function ProviderConnection({
  type,
  schema,
  configuration,
  onChange,
  headers,
  onHeadersChange,
  open,
  onOpenChange,
  onBaseUrlChange,
  onSuggestedApi,
  onAuthChange,
}: {
  type: string;
  schema: Record<string, unknown>;
  configuration: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  headers: HeaderDraft[];
  onHeadersChange: (value: HeaderDraft[]) => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onBaseUrlChange: (url: string) => void;
  onSuggestedApi: (api: string) => void;
  onAuthChange: (mode: string) => void;
}) {
  const { t } = useTranslation();
  const requiredBaseUrl =
    Array.isArray(schema.required) && schema.required.includes("base_url");
  function change(key: string, value: string) {
    const next = { ...configuration };
    if (value) next[key] = value;
    else delete next[key];
    onChange(next);
  }
  const properties = (schema.properties ?? {}) as Record<string, unknown>;
  const presets = affinityPresets(properties.session_affinity_header);
  const affinityHeader = String(configuration.session_affinity_header ?? "");
  const selectedPreset = presets.find((p) => p.header === affinityHeader);
  const configured =
    !!affinityHeader ||
    !!configuration.base_url ||
    !!configuration.mantle_base_url ||
    headers.length > 0 ||
    (type === "openai" &&
      configuration.auth_mode &&
      configuration.auth_mode !== "bearer");
  return (
    <DisclosureSection
      title={t("Advanced settings")}
      summary={configured ? t("Configured") : undefined}
      open={open}
      onOpenChange={onOpenChange}
    >
      {!requiredBaseUrl && (
        <FormField
          label={t(type === "aws_bedrock" ? "Converse base URL" : "Base URL")}
          description={t("Leave empty to use the provider's default endpoint.")}
        >
          <Input
            name="provider-base-url"
            autoComplete="off"
            placeholder="https://api.example.com/v1"
            value={String(configuration.base_url ?? "")}
            onChange={(event) => {
              change("base_url", event.target.value);
              onBaseUrlChange(event.target.value);
            }}
          />
        </FormField>
      )}
      {type === "openai" &&
        /\/(chat\/completions|responses)\/?$/.test(
          String(configuration.base_url ?? ""),
        ) && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              const url = String(configuration.base_url);
              onSuggestedApi(
                /\/responses\/?$/.test(url)
                  ? "openai.responses"
                  : "openai.chat_completions",
              );
              change(
                "base_url",
                url.replace(/\/(chat\/completions|responses)\/?$/, ""),
              );
            }}
          >
            {t("Use base URL without the API path")}
          </Button>
        )}
      {!!properties.mantle_base_url && (
        <SchemaFields
          schema={{
            ...schema,
            properties: { mantle_base_url: properties.mantle_base_url },
          }}
          value={configuration}
          onChange={onChange}
        />
      )}
      {type === "openai" && (
        <>
          <ChoiceField
            label={t("Authentication")}
            value={String(configuration.auth_mode ?? "bearer")}
            onValueChange={(value) => {
              const { api_key_header_name: _header, ...rest } = configuration;
              onChange({ ...rest, auth_mode: value });
              onAuthChange(value);
            }}
            options={[
              { value: "bearer", label: "Bearer token" },
              { value: "none", label: t("None") },
              { value: "api_key_header", label: t("Custom header") },
            ]}
          />
          {configuration.auth_mode === "api_key_header" && (
            <FormField label={t("Header name")}>
              <Input
                value={String(configuration.api_key_header_name ?? "")}
                placeholder="api-key"
                onChange={(event) =>
                  change("api_key_header_name", event.target.value)
                }
              />
            </FormField>
          )}
        </>
      )}
      {!!properties.session_affinity_header && (
        <>
          <ChoiceField
            label={t("Gateway session affinity")}
            value={
              selectedPreset
                ? affinityHeader
                : affinityHeader
                  ? "custom"
                  : "off"
            }
            description={
              selectedPreset ? t(selectedPreset.description) : undefined
            }
            onValueChange={(value) => {
              if (value !== "custom")
                change("session_affinity_header", value === "off" ? "" : value);
            }}
            options={[
              { value: "off", label: t("Disabled (default)") },
              ...presets.map((p) => ({
                value: p.header,
                label: `${t(p.label)} · ${p.header}`,
              })),
              ...(affinityHeader && !selectedPreset
                ? [{ value: "custom", label: t("Custom header") }]
                : []),
            ]}
          />
          <FormField
            label={t("Session affinity header")}
            description={t(
              "Choose a preset above or type a custom header name. The current Thread ID is supplied automatically as its value. Leave empty to disable.",
            )}
          >
            <Input
              name="session_affinity_header"
              autoComplete="off"
              maxLength={128}
              placeholder="x-conversation-id"
              value={affinityHeader}
              onChange={(event) =>
                change("session_affinity_header", event.target.value)
              }
            />
          </FormField>
          <p>
            {t(
              "Configure your gateway to route by this header. Sending it does not guarantee provider pinning; connection tests do not verify affinity.",
            )}
          </p>
        </>
      )}
      <ProviderHeaders rows={headers} onChange={onHeadersChange} />
    </DisclosureSection>
  );
}
