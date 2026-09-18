import { FormSection, formSectionStyles } from "../../shared/form-section";
import { CredentialEditor } from "../../shared/credential-editor";
import { ResourceReference } from "../../shared/resource-reference";
import { ProviderTypeField } from "../../shared/provider-type-field";
import { ProviderEnabled } from "../../shared/provider-enabled";
import { ProviderKeyLink } from "../../shared/provider-key-link";
import { providerKeyUrls } from "./provider-key-urls";
import { requiresProviderCredential } from "./provider-credentials";
import {
  ProviderConnection,
  ordinaryConfigurationSchema,
} from "./provider-connection";
import { useProviderDraft } from "./provider-draft";
import { ConnectionTest } from "./connection-test";
import { FormField, Input } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions } from "../../shared/form";
import { SchemaFields } from "../../shared/schema-fields";
import { type ModelScope } from "./api";

export function ProviderForm({
  scope,
  resource,
  definitions,
  close,
  reload,
  onCreated,
}: {
  reload: () => Promise<void>;
  scope: ModelScope;
  resource?: { value: Schema["ModelProvider"]; etag?: string };
  definitions: Schema["ModelProviderDefinition"][];
  close: () => void;
  onCreated?: (provider: Schema["ModelProvider"], modelApi?: string) => void;
}) {
  const { t } = useTranslation();
  const draft = useProviderDraft({
    scope,
    resource,
    definitions,
    close,
    onCreated,
  });
  const { original, type, definition, save } = draft;
  const credentialLabel = t(draft.credentialField.label);
  return (
    <form
      className={formSectionStyles.form}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <FormSection>
        <FormField
          className="min-w-0 w-full"
          label={t("Name")}
          labelAction={original && <ResourceReference id={original.value.id} />}
        >
          <Input
            required={true}
            value={draft.name}
            onChange={(event) => {
              draft.setName(event.target.value);
            }}
            maxLength={128}
          />
        </FormField>
      </FormSection>
      <FormSection title={t("Connection")}>
        <ProviderTypeField
          definitions={definitions}
          value={type}
          readOnly={!!original}
          onValueChange={draft.chooseType}
          labelAction={
            providerKeyUrls[type] && (
              <ProviderKeyLink {...providerKeyUrls[type]} />
            )
          }
        />
        {definition && (
          <SchemaFields
            key={type}
            schema={ordinaryConfigurationSchema(
              definition.configuration_schema,
            )}
            value={draft.configuration}
            onChange={draft.setConfiguration}
          />
        )}
        {requiresProviderCredential(type, draft.configuration, definition) && (
          <CredentialEditor
            configured={original?.value.credential_configured}
            removing={draft.removeCredential}
            onRemovingChange={(value) => {
              draft.setRemoveCredential(value);
              draft.setCredential("");
            }}
          >
            <FormField
              className="min-w-0 w-full"
              label={credentialLabel}
              description={
                original ? undefined : t(draft.credentialField.description)
              }
            >
              <Input
                type="password"
                placeholder={
                  original?.value.credential_configured
                    ? t("Saved credential · enter to replace")
                    : undefined
                }
                autoComplete="new-password"
                name="provider-api-key"
                value={draft.credential}
                onChange={(event) => {
                  draft.setCredential(event.target.value);
                  draft.setRemoveCredential(false);
                }}
              />
            </FormField>
          </CredentialEditor>
        )}
        {definition && (
          <ProviderConnection
            type={type}
            schema={definition.configuration_schema}
            configuration={draft.configuration}
            onChange={draft.setConfiguration}
            headers={draft.headers}
            onHeadersChange={draft.setHeaders}
            open={draft.advancedOpen}
            onOpenChange={draft.setAdvancedOpen}
            onBaseUrlChange={draft.changeBaseUrl}
            onSuggestedApi={draft.setSuggestedApi}
            onAuthChange={draft.changeAuth}
          />
        )}
        {original && (
          <ConnectionTest
            compact
            action={() => draft.api.testProvider(original.value.id)}
            description="May consume quota or incur cost."
            dirty={draft.changed}
          />
        )}
      </FormSection>
      {original && (
        <FormSection>
          <ProviderEnabled
            checked={draft.enabled}
            onCheckedChange={draft.setEnabled}
          />
        </FormSection>
      )}
      <ErrorNotice
        error={save.error}
        retry={original ? () => void reload() : undefined}
      />
      <FormActions
        pending={save.isPending}
        label={t(
          onCreated
            ? "Connect provider"
            : original
              ? "Save changes"
              : "Add provider",
        )}
        onCancel={close}
      />
    </form>
  );
}
