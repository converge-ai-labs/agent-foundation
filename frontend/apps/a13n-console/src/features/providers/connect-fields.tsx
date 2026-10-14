import {
  DisclosureSection,
  FormField,
  Input,
  SettingsRow,
  Switch,
} from "a13n-ui";
import { useId, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
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
  credentialSchema,
  configurationSchema,
  credential,
  onCredentialChange,
  configuration = {},
  onConfigurationChange = () => {},
  name,
  onNameChange,
  keyLink,
  advancedOpen,
  onAdvancedOpenChange,
  children,
}: {
  credentialSchema?: Record<string, unknown> | null;
  configurationSchema?: Record<string, unknown> | null;
  credential: Record<string, unknown>;
  onCredentialChange: (value: Record<string, unknown>) => void;
  /** Omitted by a category whose services configure nothing. */
  configuration?: Record<string, unknown>;
  onConfigurationChange?: (value: Record<string, unknown>) => void;
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
      {hasFields(primary) && (
        <SchemaFields
          schema={primary}
          value={configuration}
          onChange={onConfigurationChange}
        />
      )}
      <FormField
        label={t("Name")}
        description={t("How this provider is listed across the console.")}
      >
        <Input
          required
          maxLength={128}
          value={name}
          onChange={(event) => onNameChange(event.target.value)}
        />
      </FormField>
      {hasFields(advanced) && (
        <DisclosureSection
          title={hasDockerHttp ? t("Docker Engine") : t("Advanced settings")}
          summary={
            advancedConfigured(advanced, configuration)
              ? t("Configured")
              : undefined
          }
          open={advancedOpen}
          onOpenChange={onAdvancedOpenChange}
        >
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
            label={t("Envd HTTP connection")}
            description={t(
              "Configure a separate reachable address for EIP execution. Docker Engine access alone does not provide this route.",
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
            <SchemaFields
              schema={{
                ...configurationSchema,
                properties: { envd_http: properties.envd_http },
              }}
              value={configuration}
              onChange={onConfigurationChange}
            />
          )}
        </>
      )}
      {children}
    </>
  );
}
