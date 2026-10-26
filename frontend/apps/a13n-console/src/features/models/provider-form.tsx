import { ProviderDelete } from "../providers/provider-delete";
import { DisclosureSection } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import {
  FormActions,
  FormSection,
  EnabledField,
  ProviderKeyLink,
  SchemaFields,
} from "../../shared/forms";
import {
  ConnectionTest,
  CredentialRow,
  ProviderEditor,
  ProviderGroup,
  ProviderName,
  providerKeyLink,
  providerTestResult,
} from "../providers";
import {
  ProviderConnection,
  ordinaryConfigurationSchema,
} from "./provider-connection";
import { ProviderAuthorization } from "./provider-authorization";
import { useProviderDraft } from "./provider-draft";

/** Identity, connection and optional advanced settings share one staged draft. */
export function ProviderForm({
  resource,
  definitions,
  close,
  reload,
}: {
  reload: () => Promise<void>;
  resource: { value: Schema["Provider"]; etag?: string };
  definitions: Schema["ProviderType"][];
  close: () => void;
}) {
  const { t } = useTranslation();
  const draft = useProviderDraft({ resource, definitions, close });
  const { original, type, definition, save } = draft;
  const credentialLabel = t(draft.credentialField.label);
  const keyLink = providerKeyLink(definition);
  const configuration = definition && (
    <SchemaFields
      key={type}
      schema={ordinaryConfigurationSchema(definition.configuration_schema)}
      value={draft.configuration}
      onChange={draft.setConfiguration}
    />
  );
  const credential = draft.section.visible && (
    <CredentialRow
      label={credentialLabel}
      configured={draft.section.removable}
      removing={draft.section.removing}
      onRemovingChange={draft.section.setRemoving}
      onDiscard={() => draft.section.setCredential({})}
    >
      {draft.section.mode !== "forbidden" && (
        <SchemaFields
          secret
          autoFocus
          labelAction={keyLink && <ProviderKeyLink {...keyLink} />}
          schema={draft.section.schema}
          requireFields={draft.section.requireFields}
          value={draft.section.credential}
          onChange={draft.section.setCredential}
        />
      )}
    </CredentialRow>
  );
  return (
    <ProviderEditor
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <ProviderName value={draft.name} onChange={draft.setName} />
      {!definition?.oauth_scheme && credential && (
        <ProviderGroup title="Credentials">{credential}</ProviderGroup>
      )}
      {definition?.oauth_scheme && original && (
        <ProviderAuthorization
          provider={original.value}
          configurationDirty={
            JSON.stringify(draft.configuration) !==
              JSON.stringify(original.value.config) ||
            Object.keys(draft.section.credential).length > 0 ||
            draft.section.removing
          }
        />
      )}
      {definition?.oauth_scheme && (
        <FormSection title={t("Configuration")}>
          <DisclosureSection
            title={t("Advanced settings")}
            open={draft.advancedOpen}
            onOpenChange={draft.setAdvancedOpen}
          >
            {configuration}
            {credential}
          </DisclosureSection>
        </FormSection>
      )}
      {definition && !definition.oauth_scheme && (
        <FormSection title={t("Configuration")}>
          {configuration}
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
          />
        </FormSection>
      )}
      <ErrorNotice error={save.error} retry={() => void reload()} />
      {original && definition?.supports_test && (
        <ConnectionTest
          placement="footer"
          action={async () =>
            providerTestResult(await draft.api.testProvider(original.value.id))
          }
          description="May consume quota or incur cost."
          dirty={draft.changed}
        />
      )}
      <FormActions
        beforeCancel={
          <ProviderDelete
            kind="model"
            provider={resource.value}
            etag={resource.etag}
            disabled={save.isPending}
            onDeleted={close}
          />
        }
        pending={save.isPending}
        label={t("Save changes")}
        onCancel={close}
        leading={
          <EnabledField
            checked={draft.enabled}
            onCheckedChange={draft.setEnabled}
            disabled={save.isPending}
          />
        }
      />
    </ProviderEditor>
  );
}
