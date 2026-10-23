import {
  DisclosureSection,
  FormField,
  Input,
  SettingsRow,
  Switch,
} from "a13n-ui";
import { useId, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { fieldSchema } from "../../shared/forms/schema-definition";
import { ProviderKeyLink, SchemaFields } from "../../shared/forms";
import {
  advancedConfigured,
  hasFields,
  splitConfigurationSchema,
  schemaProperties,
} from "./schemas";

/**
 * The connect step every category shares: the secret the service asks for,
 * then what it cannot run without, then the name it is listed under, with the
 * settings that already have an answer folded away.
 */
export function ProviderConnectFields({
  configurationFields,
  credentialSchema,
  configurationSchema,
  credential,
  onCredentialChange,
  configuration = {},
  onConfigurationChange = () => {},
  name,
  onNameChange,
  nameDescription = "How this provider is listed across the console.",
  keyLink,
  advancedOpen,
  onAdvancedOpenChange,
  children,
}: {
  configurationFields?: ReactNode;
  credentialSchema?: Record<string, unknown> | null;
  configurationSchema?: Record<string, unknown> | null;
  credential: Record<string, unknown>;
  onCredentialChange: (value: Record<string, unknown>) => void;
  /** Omitted by a category whose services configure nothing. */
  configuration?: Record<string, unknown>;
  onConfigurationChange?: (value: Record<string, unknown>) => void;
  nameDescription?: string;
  name: string;
  onNameChange: (value: string) => void;
  /** Where the service hands out the secret, if that page is known. */
  keyLink?: { href: string; label?: string };
  /** Omitted by a category that lets the disclosure keep its own state. */
  advancedOpen?: boolean;
  onAdvancedOpenChange?: (open: boolean) => void;
  /** Category guidance that belongs between the name and the actions. */
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <>
      {hasFields(credentialSchema) && (
        <SchemaFields
          secret
          autoFocus
          labelAction={keyLink && <ProviderKeyLink {...keyLink} />}
          schema={credentialSchema ?? {}}
          value={credential}
          onChange={onCredentialChange}
        />
      )}
      {configurationFields}
      <ProviderConfigurationFields
        schema={configurationFields ? undefined : configurationSchema}
        value={configuration}
        onChange={onConfigurationChange}
        advancedOpen={advancedOpen}
        onAdvancedOpenChange={onAdvancedOpenChange}
      >
        <FormField label={t("Name")} description={t(nameDescription)}>
          <Input
            required
            maxLength={128}
            value={name}
            onChange={(event) => onNameChange(event.target.value)}
          />
        </FormField>
      </ProviderConfigurationFields>
      {children}
    </>
  );
}

/** Configuration controls shared by connection creation and editing. */
export function ProviderConfigurationFields({
  schema: configurationSchema,
  value: configuration,
  onChange: onConfigurationChange,
  advancedOpen,
  onAdvancedOpenChange,
  children,
}: {
  schema?: Record<string, unknown> | null;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  advancedOpen?: boolean;
  onAdvancedOpenChange?: (open: boolean) => void;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  const httpId = useId();
  const properties = schemaProperties(configurationSchema);
  const hasDockerHttp =
    "docker_host" in properties && "envd_http" in properties;
  const baseSchema = hasDockerHttp
    ? {
        ...configurationSchema,
        properties: Object.fromEntries(
          Object.entries(properties).filter(([key]) => key !== "envd_http"),
        ),
      }
    : configurationSchema;
  const { primary, advanced } = splitConfigurationSchema(baseSchema);
  return (
    <>
      {hasFields(primary) && (
        <SchemaFields
          schema={primary}
          value={configuration}
          onChange={onConfigurationChange}
        />
      )}
      {children}
      {hasFields(advanced) && (
        <DisclosureSection
          title={
            hasDockerHttp ? t("Container management") : t("Advanced settings")
          }
          summary={
            advancedConfigured(advanced, configuration)
              ? t("Configured")
              : undefined
          }
          open={advancedOpen}
          onOpenChange={onAdvancedOpenChange}
        >
          {hasDockerHttp && (
            <p className="text-xs text-muted-foreground">
              {t(
                "Docker Engine creates, starts, stops and deletes containers. Leave the host empty to use your deployment's default engine.",
              )}
            </p>
          )}
          <SchemaFields
            schema={advanced}
            value={configuration}
            onChange={onConfigurationChange}
          />
        </DisclosureSection>
      )}
      {hasDockerHttp && (
        <>
          <SettingsRow
            label={t("Environment operation channel")}
            description={t(
              "Enable for desktop environments. Uses envd to run commands, access files and operate the desktop. Docker Engine still manages containers; these channels work together, not as alternatives.",
            )}
            controlId={httpId}
          >
            <Switch
              id={httpId}
              checked={configuration.envd_http != null}
              onCheckedChange={(enabled) =>
                onConfigurationChange({
                  ...configuration,
                  envd_http: enabled ? { host: "" } : null,
                })
              }
            />
          </SettingsRow>
          {configuration.envd_http != null && (
            <DockerOperationFields
              schema={fieldSchema(
                properties.envd_http,
                configurationSchema ?? {},
              )}
              value={configuration.envd_http as Record<string, unknown>}
              onChange={(envd_http) =>
                onConfigurationChange({ ...configuration, envd_http })
              }
            />
          )}
        </>
      )}
    </>
  );
}

/** The host is enough for IP routes; deployment-specific overrides stay optional. */
function DockerOperationFields({
  schema,
  value,
  onChange,
}: {
  schema: Record<string, unknown>;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
}) {
  const { t } = useTranslation();
  const { primary, advanced } = splitConfigurationSchema(schema);
  return (
    <div className="grid gap-4 py-4">
      <SchemaFields schema={primary} value={value} onChange={onChange} />
      <DisclosureSection
        title={t("Advanced settings")}
        summary={
          advancedConfigured(advanced, value) ? t("Configured") : undefined
        }
      >
        <SchemaFields schema={advanced} value={value} onChange={onChange} />
      </DisclosureSection>
    </div>
  );
}
