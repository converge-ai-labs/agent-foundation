import { DisclosureSection, FormField, Input } from "a13n-ui";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ProviderKeyLink, SchemaFields } from "../../shared/forms";
import {
  advancedConfigured,
  hasFields,
  splitConfigurationSchema,
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
  advancedOpen = false,
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
  advancedOpen?: boolean;
  onAdvancedOpenChange?: (open: boolean) => void;
  /** Category guidance that belongs between the name and the actions. */
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  const { primary, advanced } = splitConfigurationSchema(configurationSchema);
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
          title={t("Advanced settings")}
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
      {children}
    </>
  );
}
