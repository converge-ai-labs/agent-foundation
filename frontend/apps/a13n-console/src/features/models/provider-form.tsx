import { ProviderEnabled } from "../../shared/provider-enabled";
import { ProviderKeyLink } from "../../shared/provider-key-link";
import { providerKeyUrls } from "./provider-key-urls";
import { requiresProviderCredential } from "./provider-credentials";
import { ConnectionTest } from "./connection-test";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  SettingsRow,
  SettingsSection,
  SearchPicker,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions } from "../../shared/form";
import { SchemaFields } from "../../shared/schema-fields";
import styles from "../../shared/shared.module.css";
import { validateSettings } from "../../shared/validation";
import { modelApi, type ModelScope } from "./api";
import { ProviderIcon } from "../../shared/provider-icon";

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
        (definitions.some((item) => item.type === "openai_compatible")
          ? "openai_compatible"
          : definitions[0]?.type) ??
        "",
    ),
    [name, setName] = useState(original?.value.name ?? ""),
    [nameEdited, setNameEdited] = useState(!!original),
    [suggestedApi, setSuggestedApi] = useState<string>(),
    [configuration, setConfiguration] = useState<Record<string, unknown>>(
      original?.value.configuration ?? {},
    ),
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
      validateSettings(definition.configuration_schema, configuration);
      const body = {
        name,
        configuration,
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
    onSuccess: (provider) => {
      setCredential("");
      void cache.invalidateQueries();
      if (onCreated) onCreated(provider, suggestedApi);
      else close();
    },
  });
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <FormField className="min-w-0 w-full" label={t("Name")}>
        <Input
          required={true}
          value={name}
          onChange={(event) => {
            setName(event.target.value);
            setNameEdited(true);
          }}
          maxLength={128}
        />
      </FormField>
      <FormField
        label={t("Provider type")}
        labelAction={
          providerKeyUrls[type] && (
            <ProviderKeyLink {...providerKeyUrls[type]} />
          )
        }
      >
        <SearchPicker
          placeholder={t("Search providers…")}
          emptyMessage={t("No matching providers")}
          value={type}
          disabled={!!original}
          onValueChange={(value) => {
            setType(value);
            setConfiguration({});
            setCredential("");
            setRemoveCredential(false);
            setSuggestedApi(undefined);
          }}
          label={t("Provider type")}
          groups={[
            {
              label: "",
              options: definitions.map((item) => ({
                value: item.type,
                label: item.display_name,
                icon: <ProviderIcon key={item.type} type={item.type} />,
                keywords: [item.type],
              })),
            },
          ]}
        />
      </FormField>
      {type === "openai_compatible" ? (
        <>
          <FormField label={t("Base URL")}>
            <Input
              required
              type="url"
              name="provider-base-url"
              autoComplete="off"
              placeholder="https://api.example.com/v1"
              value={String(configuration.base_url ?? "")}
              onChange={(event) => {
                setSuggestedApi(undefined);
                if (!nameEdited) {
                  try {
                    setName(new URL(event.target.value).hostname.slice(0, 128));
                  } catch {
                    /* Keep the suggestion while a URL is incomplete. */
                  }
                }
                setConfiguration({
                  ...configuration,
                  base_url: event.target.value,
                });
              }}
            />
          </FormField>
          {/\/(chat\/completions|responses)\/?$/.test(
            String(configuration.base_url ?? ""),
          ) && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                setSuggestedApi(
                  /\/responses\/?$/.test(String(configuration.base_url))
                    ? "openai.responses"
                    : "openai.chat_completions",
                );
                setConfiguration({
                  ...configuration,
                  base_url: String(configuration.base_url).replace(
                    /\/(chat\/completions|responses)\/?$/,
                    "",
                  ),
                });
              }}
            >
              {t("Use base URL without the API path")}
            </Button>
          )}
        </>
      ) : (
        definition && (
          <SchemaFields
            key={type}
            schema={definition.configuration_schema}
            value={configuration}
            onChange={setConfiguration}
          />
        )
      )}
      {requiresProviderCredential(type, configuration, definition) && (
        <FormField
          className="min-w-0 w-full"
          label={credentialLabel}
          description={[
            t(credentialField.description),
            original?.value.credential_configured
              ? t("Leave empty to keep the saved {{label}}.", {
                  label: credentialLabel,
                })
              : "",
          ]
            .filter(Boolean)
            .join(" ")}
        >
          <Input
            type="password"
            autoComplete="new-password"
            name="provider-api-key"
            value={credential}
            onChange={(event) => {
              setCredential(event.target.value);
              setRemoveCredential(false);
            }}
          />
        </FormField>
      )}
      {type === "openai_compatible" && (
        <DisclosureSection title={t("Authentication")}>
          <ChoiceField
            label={t("Authentication")}
            value={String(configuration.auth_mode ?? "bearer")}
            onValueChange={(value) => {
              const { api_key_header_name: _header, ...rest } = configuration;
              setConfiguration({ ...rest, auth_mode: value });
              if (value === "none") {
                setCredential("");
                setRemoveCredential(true);
              } else setRemoveCredential(false);
            }}
            options={[
              { value: "bearer", label: "Bearer token" },
              { value: "none", label: t("None") },
              { value: "api_key_header", label: t("Custom header") },
            ]}
          />
          {configuration.auth_mode === "api_key_header" && (
            <FormField label={t("Header name")}>
              <Input
                required
                placeholder="api-key"
                value={String(configuration.api_key_header_name ?? "")}
                onChange={(event) =>
                  setConfiguration({
                    ...configuration,
                    api_key_header_name: event.target.value,
                  })
                }
              />
            </FormField>
          )}
        </DisclosureSection>
      )}
      {original && (
        <SettingsSection>
          <ProviderEnabled checked={enabled} onCheckedChange={setEnabled} />
          {original.value.credential_configured && (
            <SettingsRow
              stackOnNarrow={false}
              label={t("Saved credentials")}
              description={t(
                removeCredential
                  ? "Credentials will be removed when you save."
                  : "Replace them above, or remove the saved credentials.",
              )}
            >
              <Button
                variant="ghost"
                size="sm"
                className={removeCredential ? undefined : "text-destructive"}
                onClick={() => setRemoveCredential(!removeCredential)}
              >
                {t(removeCredential ? "Undo" : "Remove")}
              </Button>
            </SettingsRow>
          )}
          <ConnectionTest
            action={() => api.testProvider(original.value.id)}
            description="Check the saved connection. May consume provider quota."
            dirty={
              save.isPending ||
              name !== original.value.name ||
              enabled !== original.value.enabled ||
              !!credential ||
              removeCredential ||
              JSON.stringify(configuration) !==
                JSON.stringify(original.value.configuration)
            }
          />
        </SettingsSection>
      )}
      <ErrorNotice
        error={save.error}
        retry={original ? () => void reload() : undefined}
      />
      <FormActions
        pending={save.isPending}
        label={onCreated ? t("Connect provider") : undefined}
        onCancel={close}
      />
    </form>
  );
}
