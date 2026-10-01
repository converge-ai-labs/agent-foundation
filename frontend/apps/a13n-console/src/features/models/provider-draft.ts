import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { type Schema } from "../../shared/api";
import {
  useSuggestedName,
  validateSettings,
  withSchemaValues,
} from "../../shared/forms";
import { useCredentialSection } from "../../shared/use-credential-section";
import { credentialDescription, credentialLabel } from "../providers";
import { modelApi } from "./api";
import {
  initialHeaders,
  newHeaders,
  serializeHeaders,
} from "./provider-headers";

/** Named by the credential schema each definition declares, never by vendor. */
export function credentialFieldFor(definition?: Schema["ProviderType"]) {
  return {
    label: credentialLabel(definition?.credential_schema),
    description: credentialDescription(definition?.credential_schema),
  };
}

/** Draft state and save mutation shared by the provider editors. */
export function useProviderDraft({
  resource,
  definitions,
  initialType,
  close,
  onCreated,
}: {
  resource?: { value: Schema["Provider"]; etag?: string };
  definitions: Schema["ProviderType"][];
  initialType?: string;
  close: () => void;
  onCreated?: (provider: Schema["Provider"], modelApi?: string) => void;
}) {
  const [createdProvider, setCreatedProvider] = useState<Schema["Provider"]>();
  const [original] = useState(resource),
    { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    api = modelApi(client, workspace.id);
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
      original?.value.config ?? {},
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
      const config = withSchemaValues(
        definition.configuration_schema,
        configuration,
      );
      validateSettings(definition.configuration_schema, config);
      const credential = section.payload();
      if (credential) validateSettings(section.schema, credential);
      const body = {
        name,
        config,
        enabled,
        ...(credential === undefined ? {} : { credential }),
      };
      if (!original)
        return api.createProvider({
          ...body,
          type,
          extra_headers: newHeaders(headers),
        });
      if (!original.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      return api.updateProvider(original.value.id, original.etag, {
        ...body,
        extra_headers: serializeHeaders(headers, original.value.header_names),
      });
    },
    onError: () => setAdvancedOpen(true),
    onSuccess: (provider) => {
      setHeaders([]);
      section.setCredential({});
      void cache.invalidateQueries();
      if (onCreated) onCreated(provider, suggestedApi);
      else if (!original && definition?.oauth_scheme)
        setCreatedProvider(provider);
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
    JSON.stringify(configuration) !== JSON.stringify(original.value.config);
  return {
    api,
    createdProvider,
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
