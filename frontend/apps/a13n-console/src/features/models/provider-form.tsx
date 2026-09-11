import { useSuggestedName } from "../../shared/suggested-name";
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
import { SchemaFields } from "../../shared/schema-fields";
import { validateSettings } from "../../shared/validation";
import { modelApi, type ModelScope } from "./api";

const credentialFields: Record<string, { label: string; description: string }> =
  {
    api_key: {
      label: "API key",
      description: "Paste the API key from your provider account.",
    },
    google_service_account_json: {
      label: "Service account JSON",
      description:
        "Paste the complete JSON key file for your Google Cloud service account.",
    },
    aws_credentials_json: {
      label: "AWS access keys (JSON)",
      description:
        "Paste a JSON object with aws_access_key_id and aws_secret_access_key. Include aws_session_token for temporary credentials.",
    },
  };

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
    [credential, setCredential] = useState(""),
    [removeCredential, setRemoveCredential] = useState(false),
    [enabled, setEnabled] = useState(original?.value.enabled ?? true);
  const definition = definitions.find((item) => item.type === type);
  const credentialField = credentialFields[
    String(
      definition?.credential_schema["x-a13n-credential-format"] ?? "api_key",
    )
  ] ?? { label: "Authentication secret", description: "" };
  const credentialLabel = t(credentialField.label);
  const save = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      if (!definition) throw new Error(t("Choose a provider type."));
      const extraHeaders = serializeHeaders(
        headers,
        original?.value.header_names ?? [],
      );
      validateSettings(definition.configuration_schema, configuration);
      const body = {
        name,
        configuration,
        extra_headers: extraHeaders,
        enabled,
        ...(removeCredential
          ? { credential: null }
          : credential
            ? { credential }
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
      setCredential("");
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
      <FormSection aside={!onCreated} title={t("General")}>
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
      <FormSection aside={!onCreated} title={t("Connection")}>
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
            setCredential("");
            setRemoveCredential(false);
            setSuggestedApi(undefined);
          }}
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
            value={configuration}
            onChange={setConfiguration}
          />
        )}
        {requiresProviderCredential(type, configuration, definition) && (
          <CredentialEditor
            configured={original?.value.credential_configured}
            removing={removeCredential}
            onRemovingChange={(value) => {
              setRemoveCredential(value);
              setCredential("");
            }}
          >
            <FormField
              className="min-w-0 w-full"
              label={credentialLabel}
              description={
                original ? undefined : t(credentialField.description)
              }
            >
              <Input
                type="password"
                placeholder={
                  original?.value.credential_configured
                    ? t("Leave empty to keep saved credential")
                    : undefined
                }
                autoComplete="new-password"
                name="provider-api-key"
                value={credential}
                onChange={(event) => {
                  setCredential(event.target.value);
                  setRemoveCredential(false);
                }}
              />
            </FormField>
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
            onAuthChange={(mode) => {
              if (mode === "none") setCredential("");
              setRemoveCredential(mode === "none");
            }}
          />
        )}
        {original && (
          <ConnectionTest
            compact
            action={() => api.testProvider(original.value.id)}
            description="May consume quota or incur cost."
            dirty={
              save.isPending ||
              name !== original.value.name ||
              enabled !== original.value.enabled ||
              !!credential ||
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
        <FormSection aside={!onCreated} title={t("Availability")}>
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
