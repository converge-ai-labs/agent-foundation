import { useSuggestedName } from "../../shared/suggested-name";
import { FormSection, formSectionStyles } from "../../shared/form-section";
import { CredentialEditor } from "../../shared/credential-editor";
import { ResourceReference } from "../../shared/resource-reference";
import { ProviderTypeField } from "../../shared/provider-type-field";
import { ProviderEnabled } from "../../shared/provider-enabled";
import { ProviderKeyLink } from "../../shared/provider-key-link";
import {
  credentialMode,
  providerSchema,
} from "../../shared/provider-authentication";
import {
  ProviderConnection,
  ordinaryConfigurationSchema,
} from "./provider-connection";
import { initialHeaders, serializeHeaders } from "./provider-headers";
import { ConnectionTest } from "./connection-test";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FormField, Input } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions } from "../../shared/form";
import { SchemaFields, withSchemaValues } from "../../shared/schema-fields";
import { validateSettings } from "../../shared/validation";
import { modelApi, type ModelScope } from "./api";

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
  definitions: Schema["ModelProviderMetadata"][];
  close: () => void;
  onCreated?: (provider: Schema["ModelProvider"], modelApi?: string) => void;
}) {
  const [original] = useState(resource),
    { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient(),
    api = modelApi(client, scope);
  const [type, setType] = useState(
      original?.value.type ??
        (definitions.some((item) => item.type === "openai")
          ? "openai"
          : definitions[0]?.type) ??
        "",
    ),
    { name, setName, suggestName } = useSuggestedName(original?.value.name),
    [suggestedApi, setSuggestedApi] = useState<string>(),
    [configuration, setConfiguration] = useState<Record<string, unknown>>(
      original?.value.configuration ?? {},
    ),
    [headers, setHeaders] = useState(() => initialHeaders(original?.value)),
    [advancedOpen, setAdvancedOpen] = useState(false),
    [credential, setCredential] = useState<Record<string, unknown>>({}),
    [removeCredential, setRemoveCredential] = useState(false),
    [enabled, setEnabled] = useState(original?.value.enabled ?? true);
  const definition = definitions.find((item) => item.type === type);
  const mode = credentialMode(definition, configuration);
  const save = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      if (!definition) throw new Error(t("Choose a provider type."));
      const extraHeaders = serializeHeaders(
        headers,
        original?.value.header_names ?? [],
      );
      const config = withSchemaValues(
        definition.configuration_schema,
        configuration,
      );
      validateSettings(definition.configuration_schema, config);
      const replacement = withSchemaValues(
        providerSchema(definition.credential_schema),
        credential,
      );
      const replacing =
        Object.keys(credential).length > 0 ||
        (mode === "required" && !original?.value.credential_configured);
      if (
        mode !== "forbidden" &&
        (replacing ||
          (mode === "required" && !original?.value.credential_configured))
      )
        validateSettings(
          providerSchema(definition.credential_schema),
          replacement,
        );
      const body = {
        name,
        configuration: config,
        extra_headers: extraHeaders,
        enabled,
        ...(removeCredential ||
        (mode === "forbidden" && original?.value.credential_configured)
          ? { credential: null }
          : mode !== "forbidden" && replacing
            ? { credential: replacement }
            : {}),
      };
      if (!original) return api.createProvider({ ...body, type });
      if (!original.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      return api.updateProvider(original.value.id, original.etag, body);
    },
    onError: () => setAdvancedOpen(true),
    onSuccess: (provider) => {
      setHeaders([]);
      setCredential({});
      void cache.invalidateQueries();
      if (onCreated) onCreated(provider, suggestedApi);
      else close();
    },
  });
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
            value={name}
            onChange={(event) => {
              setName(event.target.value);
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
          onValueChange={(value) => {
            setType(value);
            suggestName(
              definitions.find((item) => item.type === value)?.display_name ??
                value,
            );
            setConfiguration({});
            setHeaders([]);
            setAdvancedOpen(false);
            setCredential({});
            setRemoveCredential(false);
            setSuggestedApi(undefined);
          }}
          labelAction={
            definition?.setup_url && (
              <ProviderKeyLink
                href={definition.setup_url}
                label={definition.setup_label ?? undefined}
              />
            )
          }
        />
        {definition && (
          <SchemaFields
            key={type}
            schema={ordinaryConfigurationSchema(
              definition.configuration_schema,
            )}
            value={configuration}
            onChange={setConfiguration}
          />
        )}
        {definition && mode !== "forbidden" && (
          <CredentialEditor
            configured={original?.value.credential_configured}
            removing={removeCredential}
            onRemovingChange={(value) => {
              setRemoveCredential(value);
              setCredential({});
            }}
          >
            <SchemaFields
              schema={providerSchema(definition.credential_schema)}
              value={credential}
              onChange={(value) => {
                setCredential(value);
                setRemoveCredential(false);
              }}
              secret
              requireFields={
                mode === "required" &&
                (!original?.value.credential_configured ||
                  Object.keys(credential).length > 0)
              }
            />
          </CredentialEditor>
        )}
        {definition && (
          <ProviderConnection
            type={type}
            schema={definition.configuration_schema}
            configuration={configuration}
            onChange={setConfiguration}
            headers={headers}
            onHeadersChange={setHeaders}
            open={advancedOpen}
            onOpenChange={setAdvancedOpen}
            onBaseUrlChange={(url) => {
              setSuggestedApi(undefined);
              try {
                suggestName(new URL(url).hostname.slice(0, 128));
              } catch {
                /* URL may be incomplete. */
              }
            }}
            onSuggestedApi={setSuggestedApi}
          />
        )}
        {original && definition?.supports_connection_probe && (
          <ConnectionTest
            compact
            action={() => api.testProvider(original.value.id)}
            description="May consume quota or incur cost."
            dirty={
              save.isPending ||
              name !== original.value.name ||
              enabled !== original.value.enabled ||
              Object.keys(credential).length > 0 ||
              JSON.stringify(headers) !==
                JSON.stringify(initialHeaders(original.value)) ||
              removeCredential ||
              JSON.stringify(configuration) !==
                JSON.stringify(original.value.configuration)
            }
          />
        )}
      </FormSection>
      {original && (
        <FormSection>
          <ProviderEnabled checked={enabled} onCheckedChange={setEnabled} />
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
