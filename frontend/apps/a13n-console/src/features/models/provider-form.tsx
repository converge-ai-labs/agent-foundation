import { FormField, Input } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import {
  FormActions,
  ProviderEnabled,
  ProviderKeyLink,
  SchemaFields,
} from "../../shared/forms";
import {
  ConnectionTest,
  CredentialRow,
  ProviderEditor,
  ProviderFacts,
  ProviderGroup,
  ProviderName,
  providerKeyUrls,
} from "../providers";
import { type ModelScope } from "./api";
import {
  ProviderConnection,
  ordinaryConfigurationSchema,
} from "./provider-connection";
import { requiresProviderCredential } from "./provider-credentials";
import { useProviderDraft } from "./provider-draft";

/** The name, one settings group, then the advanced disclosure. */
export function ProviderForm({
  scope,
  resource,
  definitions,
  close,
  reload,
}: {
  reload: () => Promise<void>;
  scope: ModelScope;
  resource: { value: Schema["ModelProvider"]; etag?: string };
  definitions: Schema["ModelProviderDefinition"][];
  close: () => void;
}) {
  const { t } = useTranslation();
  const draft = useProviderDraft({ scope, resource, definitions, close });
  const { original, type, definition, save } = draft;
  const credentialLabel = t(draft.credentialField.label);
  const keyLink = providerKeyUrls[type];
  return (
    <ProviderEditor
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <ProviderName value={draft.name} onChange={draft.setName} />
      {definition && (
        <SchemaFields
          key={type}
          schema={ordinaryConfigurationSchema(definition.configuration_schema)}
          value={draft.configuration}
          onChange={draft.setConfiguration}
        />
      )}
      <ProviderGroup>
        <ProviderEnabled
          checked={draft.enabled}
          onCheckedChange={draft.setEnabled}
        />
        {requiresProviderCredential(type, draft.configuration, definition) && (
          <CredentialRow
            label={credentialLabel}
            configured={!!original?.value.credential_configured}
            removing={draft.removeCredential}
            onRemovingChange={draft.setRemoveCredential}
            onDiscard={() => draft.setCredential("")}
          >
            <FormField
              className="min-w-0 w-full"
              label={credentialLabel}
              labelAction={keyLink && <ProviderKeyLink {...keyLink} />}
            >
              <Input
                type="password"
                autoFocus
                autoComplete="new-password"
                name="provider-api-key"
                value={draft.credential}
                onChange={(event) => {
                  draft.setCredential(event.target.value);
                  draft.setRemoveCredential(false);
                }}
              />
            </FormField>
          </CredentialRow>
        )}
        <ProviderFacts
          configuration={draft.configuration}
          schema={definition?.configuration_schema}
          only={["base_url"]}
        />
      </ProviderGroup>
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
      <ErrorNotice error={save.error} retry={() => void reload()} />
      <FormActions
        pending={save.isPending}
        label={t("Save changes")}
        onCancel={close}
        leading={
          original && (
            <ConnectionTest
              placement="footer"
              action={() => draft.api.testProvider(original.value.id)}
              description="May consume quota or incur cost."
              dirty={draft.changed}
            />
          )
        }
      />
    </ProviderEditor>
  );
}
