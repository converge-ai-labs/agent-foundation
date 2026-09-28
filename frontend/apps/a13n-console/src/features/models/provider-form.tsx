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
  providerKeyLink,
  providerTestResult,
} from "../providers";
import {
  ProviderConnection,
  ordinaryConfigurationSchema,
} from "./provider-connection";
import { useProviderDraft } from "./provider-draft";

/** The name, one settings group, then the advanced disclosure. */
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
        {draft.section.visible && (
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
        />
      )}
      <ErrorNotice error={save.error} retry={() => void reload()} />
      <FormActions
        pending={save.isPending}
        label={t("Save changes")}
        onCancel={close}
        leading={
          original &&
          definition?.supports_test && (
            <ConnectionTest
              placement="footer"
              action={async () =>
                providerTestResult(
                  await draft.api.testProvider(original.value.id),
                )
              }
              description="May consume quota or incur cost."
              dirty={draft.changed}
            />
          )
        }
      />
    </ProviderEditor>
  );
}
