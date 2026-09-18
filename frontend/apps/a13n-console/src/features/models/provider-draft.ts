import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { type Schema } from "../../shared/api";
import { useSuggestedName } from "../../shared/suggested-name";
import { validateSettings } from "../../shared/validation";
import { modelApi, type ModelScope } from "./api";
import { initialHeaders, serializeHeaders } from "./provider-headers";

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

export function credentialFieldFor(
  definition?: Schema["ModelProviderDefinition"],
) {
  return (
    credentialFields[
      String(
        definition?.credential_schema["x-a13n-credential-format"] ?? "api_key",
      )
    ] ?? { label: "Authentication secret", description: "" }
  );
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
  definitions: Schema["ModelProviderDefinition"][];
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
    [credential, setCredential] = useState(""),
    [removeCredential, setRemoveCredential] = useState(false),
    [enabled, setEnabled] = useState(original?.value.enabled ?? true);
  const definition = definitions.find((item) => item.type === type);
  const credentialField = credentialFieldFor(definition);
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
  function chooseType(value: string) {
    setType(value);
    suggestName(
      definitions.find((item) => item.type === value)?.display_name ?? value,
    );
    setConfiguration({});
    setHeaders([]);
    setAdvancedOpen(false);
    setCredential("");
    setRemoveCredential(false);
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
  function changeAuth(mode: string) {
    if (mode === "none") setCredential("");
    setRemoveCredential(mode === "none");
  }
  const changed =
    !original ||
    save.isPending ||
    name !== original.value.name ||
    enabled !== original.value.enabled ||
    !!credential ||
    JSON.stringify(headers) !==
      JSON.stringify(initialHeaders(original.value)) ||
    removeCredential ||
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
    credential,
    setCredential,
    removeCredential,
    setRemoveCredential,
    enabled,
    setEnabled,
    setSuggestedApi,
    changeBaseUrl,
    changeAuth,
    changed,
    save,
  };
}
