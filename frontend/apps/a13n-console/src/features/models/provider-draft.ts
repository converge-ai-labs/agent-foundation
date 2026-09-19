import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { type Schema } from "../../shared/api";
import {
  useSuggestedName,
  validateSettings,
  withSchemaValues,
} from "../../shared/forms";
import { useCredentialSection } from "../../shared/use-credential-section";
import { credentialDescription, credentialLabel } from "../providers";
import { modelApi, type ModelScope } from "./api";
import { initialHeaders, serializeHeaders } from "./provider-headers";

/** Named by the credential schema each definition declares, never by vendor. */
export function credentialFieldFor(
  definition?: Schema["ModelProviderMetadata"],
) {
  return {
    label: credentialLabel(definition?.credential_schema),
    description: credentialDescription(definition?.credential_schema),
  };
}

/** Draft state and save mutation shared by the provider editors. */
export function useProviderDraft({
  scope,
  resource,
  definitions,
  initialType,
  close,
  onCreated,
}: {
  scope: ModelScope;
  resource?: { value: Schema["ModelProvider"]; etag?: string };
  definitions: Schema["ModelProviderMetadata"][];
  initialType?: string;
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
        initialType ??
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
    [enabled, setEnabled] = useState(original?.value.enabled ?? true);
  const definition = definitions.find((item) => item.type === type);
  const section = useCredentialSection(
    definition,
    configuration,
    original?.value,
  );
  const credentialField = credentialFieldFor(definition);
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
      const credential = section.payload();
      if (credential) validateSettings(section.schema, credential);
      const body = {
        name,
        configuration: config,
        extra_headers: extraHeaders,
        enabled,
        ...(credential === undefined ? {} : { credential }),
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
      section.setCredential({});
      void cache.invalidateQueries();
      if (onCreated) onCreated(provider, suggestedApi);
      else close();
    },
  });
  function chooseType(value: string) {
    setType(value);
    suggestName(
      definitions.find((item) => item.type === value)?.display_name ?? value,
    );
    setConfiguration({});
    setHeaders([]);
    setAdvancedOpen(false);
    section.setCredential({});
    section.setRemoving(false);
    setSuggestedApi(undefined);
  }
  function changeBaseUrl(url: string) {
    setSuggestedApi(undefined);
    try {
      suggestName(new URL(url).hostname.slice(0, 128));
    } catch {
      /* URL may be incomplete. */
    }
  }
  const changed =
    !original ||
    save.isPending ||
    name !== original.value.name ||
    enabled !== original.value.enabled ||
    Object.keys(section.credential).length > 0 ||
    JSON.stringify(headers) !==
      JSON.stringify(initialHeaders(original.value)) ||
    section.removing ||
    JSON.stringify(configuration) !==
      JSON.stringify(original.value.configuration);
  return {
    api,
    close,
    original,
    type,
    chooseType,
    definition,
    credentialField,
    name,
    setName,
    configuration,
    setConfiguration,
    headers,
    setHeaders,
    advancedOpen,
    setAdvancedOpen,
    section,
    enabled,
    setEnabled,
    setSuggestedApi,
    changeBaseUrl,
    changed,
    save,
  };
}
