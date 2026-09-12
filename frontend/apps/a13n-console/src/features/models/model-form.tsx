import { ResourceReference } from "../../shared/resource-reference";
import { ConnectionTest } from "./connection-test";
import { ProviderIcon } from "../../shared/provider-icon";
import { apiLabel, suggestedKey } from "./model-options";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  SearchPicker,
  Switch,
  Tabs,
  TabsList,
  TabsTab,
} from "a13n-ui";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { allPages, data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions } from "../../shared/form";
import styles from "../../shared/shared.module.css";
import { jsonObject, validateSettings } from "../../shared/validation";
import { modelApi, type ModelScope } from "./api";
import { ModelIcon } from "./model-icon";
import { ModelParameters } from "./model-parameters";
import { ProviderSetup } from "./provider-setup";
import modelStyles from "./models.module.css";

export function ModelForm({
  scope,
  resource,
  providerId,
  candidate,
  close,
  reload,
  onSaved,
}: {
  scope: ModelScope;
  resource?: { value: Schema["Model"]; etag?: string };
  providerId?: string;
  candidate?: Schema["ModelCandidate"];
  close: () => void;
  reload: () => Promise<void>;
  onSaved?: (model: Schema["Model"]) => void;
}) {
  const [original] = useState(resource),
    client = useClient(),
    { t } = useTranslation(),
    cache = useQueryClient(),
    api = modelApi(client, scope);
  const [provider, setProvider] = useState(
      original?.value.provider_id ?? providerId ?? "",
    ),
    [choosingProvider, setChoosingProvider] = useState(
      !original && !providerId,
    ),
    [upstream, setUpstream] = useState(
      original?.value.upstream_model ?? candidate?.upstream_model ?? "",
    ),
    [name, setName] = useState(
      original?.value.name ??
        candidate?.display_name ??
        candidate?.upstream_model ??
        "",
    ),
    [key, setKey] = useState(
      original?.value.key ?? suggestedKey(candidate?.upstream_model ?? ""),
    ),
    [nameEdited, setNameEdited] = useState(!!original),
    [keyEdited, setKeyEdited] = useState(!!original),
    [apiEdited, setApiEdited] = useState(!!original),
    [modelApiKey, setModelApiKey] = useState(
      original?.value.model_api ?? candidate?.suggested_model_api ?? "",
    ),
    [settingsText, setSettingsText] = useState(
      JSON.stringify(
        original?.value.settings ?? candidate?.suggested_settings ?? {},
        null,
        2,
      ),
    ),
    [description, setDescription] = useState(original?.value.description ?? ""),
    [enabled, setEnabled] = useState(original?.value.enabled ?? true),
    [parameterError, setParameterError] = useState<string>(),
    [manual, setManual] = useState(!!original || !!candidate),
    [settledUpstream, setSettledUpstream] = useState(upstream);
  const providers = useQuery({
    queryKey: ["model-provider-choices", scope.kind, scope.id],
    queryFn: ({ signal }) =>
      allPages((cursor) => api.providers(signal, cursor)),
  });
  const definitions = useQuery({
    queryKey: ["model-provider-types"],
    queryFn: ({ signal }) =>
      client.http.GET("/api/v1/model-provider-types", { signal }).then(data),
  });
  const selectedProvider = providers.data?.find((item) => item.id === provider);
  const definition = definitions.data?.items.find(
    (item) => item.type === selectedProvider?.type,
  );
  const callingApi = modelApiKey || definition?.default_model_api || "";
  const catalog = useQuery({
    queryKey: ["model-catalog", scope.kind, scope.id, provider],
    queryFn: () => api.discover(provider),
    enabled:
      !!provider &&
      !choosingProvider &&
      !manual &&
      !!definition?.supports_model_discovery,
    retry: false,
  });
  useEffect(() => {
    const timer = setTimeout(() => setSettledUpstream(upstream.trim()), 400);
    return () => clearTimeout(timer);
  }, [upstream]);
  const describe = useQuery({
    queryKey: [
      "model-description",
      scope.kind,
      scope.id,
      provider,
      settledUpstream,
      callingApi,
    ],
    queryFn: () =>
      api.describe(provider, {
        upstream_model: settledUpstream,
        model_api: callingApi || undefined,
      }),
    enabled:
      !!provider &&
      !!settledUpstream &&
      !!callingApi &&
      settledUpstream === upstream.trim(),
    retry: false,
  });
  const metadata =
    settledUpstream === upstream.trim() ? describe.data : undefined;
  function chooseUpstream(
    value: string,
    suggestion?: Schema["ModelCandidate"],
  ) {
    setUpstream(value);
    if (!nameEdited) setName((suggestion?.display_name ?? value).slice(0, 128));
    if (!keyEdited) setKey(suggestedKey(value));
    if (suggestion) {
      if (!apiEdited) setModelApiKey(suggestion.suggested_model_api);
      if (settingsText.trim() === "{}")
        setSettingsText(JSON.stringify(suggestion.suggested_settings, null, 2));
    }
  }
  function chooseProvider(value: string) {
    if (value !== provider) {
      setProvider(value);
      setModelApiKey("");
      setApiEdited(false);
      setUpstream("");
      setSettledUpstream("");
      setSettingsText("{}");
      if (!nameEdited) setName("");
      if (!keyEdited) setKey("");
      setManual(false);
    }
    setChoosingProvider(false);
  }
  const save = useMutation({
    mutationFn: async () => {
      let settings: ReturnType<typeof jsonObject>;
      try {
        settings = jsonObject(settingsText);
        if (metadata) validateSettings(metadata.settings_schema, settings);
        setParameterError(undefined);
      } catch (error) {
        setParameterError(
          error instanceof Error ? error.message : t("Invalid JSON"),
        );
        throw error;
      }
      const body = {
        name,
        upstream_model: upstream.trim(),
        model_api: callingApi,
        settings,
        description: description || null,
        enabled,
      };
      if (!original)
        return api.createModel({ ...body, key, provider_id: provider });
      if (!original.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      return api.updateModel(original.value.id, original.etag, body);
    },
    onSuccess: (model) => {
      void cache.invalidateQueries();
      onSaved?.(model);
      close();
    },
  });
  const dirty =
    !!original &&
    (upstream.trim() !== original.value.upstream_model ||
      callingApi !== original.value.model_api ||
      name !== original.value.name ||
      description !== (original.value.description ?? "") ||
      enabled !== original.value.enabled ||
      settingsText !== JSON.stringify(original.value.settings, null, 2));
  if (choosingProvider)
    return (
      <ProviderSetup
        onCancel={close}
        scope={scope}
        providers={providers.data}
        definitions={definitions.data?.items}
        value={provider}
        error={providers.error ?? definitions.error}
        onSelect={chooseProvider}
        onCreated={(item, preferredApi) => {
          cache.setQueryData<Schema["ModelProvider"][]>(
            ["model-provider-choices", scope.kind, scope.id],
            (items) => [
              ...(items ?? []).filter((value) => value.id !== item.id),
              item,
            ],
          );
          chooseProvider(item.id);
          if (preferredApi) {
            setModelApiKey(preferredApi);
            setApiEdited(true);
          }
        }}
      />
    );
  const identityFields = (
    <section
      className={
        original ? modelStyles.editIdentity : modelStyles.identityFields
      }
    >
      <div className={original ? styles.stack : styles.twoColumns}>
        <FormField
          label={t("Name")}
          labelAction={
            original && (
              <ResourceReference
                id={original.value.id}
                resourceKey={original.value.key}
              />
            )
          }
        >
          <Input
            required
            maxLength={128}
            value={name}
            onChange={(event) => {
              setName(event.target.value);
              setNameEdited(true);
            }}
          />
        </FormField>
        {!original && (
          <FormField
            label={t("Model key")}
            description={t("Used by agents. Cannot be changed later.")}
          >
            <Input
              required
              maxLength={128}
              value={key}
              onChange={(event) => {
                setKey(event.target.value);
                setKeyEdited(true);
              }}
            />
          </FormField>
        )}
      </div>
      <FormField label={t("Description")}>
        <Input
          value={description}
          placeholder={t("Optional")}
          onChange={(event) => setDescription(event.target.value)}
        />
      </FormField>
    </section>
  );
  const connectionFields = (
    <section className={modelStyles.connectionFields}>
      <div
        className={`${modelStyles.connectionSummary} ${modelStyles.providerSummary}`}
      >
        {selectedProvider && (
          <ProviderIcon
            key={selectedProvider.type}
            type={selectedProvider.type}
          />
        )}
        <div>
          <strong>{selectedProvider?.name ?? t("Loading…")}</strong>
          {typeof selectedProvider?.configuration.base_url === "string" && (
            <span>{selectedProvider.configuration.base_url}</span>
          )}
        </div>
        {!original && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setChoosingProvider(true)}
          >
            {t("Change")}
          </Button>
        )}
      </div>
      {!original && definition?.supports_model_discovery && (
        <Tabs
          value={manual ? "manual" : "catalog"}
          onValueChange={(value) => setManual(value === "manual")}
        >
          <TabsList aria-label={t("Model source")}>
            <TabsTab value="catalog">{t("From catalog")}</TabsTab>
            <TabsTab value="manual">{t("Enter model ID")}</TabsTab>
          </TabsList>
        </Tabs>
      )}
      <div className={styles.twoColumns}>
        {!original && !manual && definition?.supports_model_discovery ? (
          <div className={styles.stack}>
            {catalog.isPending ? (
              <Loading />
            ) : catalog.data?.items.length ? (
              <FormField label={t("Model")}>
                <SearchPicker
                  label={t("Model")}
                  placeholder={t("Choose a model…")}
                  emptyMessage={t(
                    "No models found. You can still add a model manually.",
                  )}
                  value={upstream}
                  onValueChange={(value) =>
                    chooseUpstream(
                      value,
                      catalog.data?.items.find(
                        (item) => item.upstream_model === value,
                      ),
                    )
                  }
                  groups={[
                    {
                      label: t("Models"),
                      options: catalog.data.items.map((item) => ({
                        value: item.upstream_model,
                        label: item.display_name ?? item.upstream_model,
                        description:
                          item.display_name &&
                          item.display_name !== item.upstream_model
                            ? item.upstream_model
                            : undefined,
                      })),
                    },
                  ]}
                />
              </FormField>
            ) : (
              <p className={styles.muted}>
                {t("Catalog unavailable. Enter a model ID to continue.")}
              </p>
            )}
            {(catalog.error ||
              (!catalog.data?.items.length && !catalog.isPending)) && (
              <Button
                type="button"
                variant="outline"
                onClick={() => setManual(true)}
              >
                {t("Enter model ID")}
              </Button>
            )}
          </div>
        ) : (
          <FormField label={t("Upstream model")}>
            <Input
              required
              value={upstream}
              placeholder="e.g. gpt-4.1"
              maxLength={256}
              onChange={(event) => chooseUpstream(event.target.value)}
            />
          </FormField>
        )}
        {(definition?.supported_model_apis.length ?? 0) > 1 && (
          <ChoiceField
            label={t("API")}
            value={callingApi}
            onValueChange={(value) => {
              setModelApiKey(value);
              setApiEdited(true);
            }}
            options={
              definition?.supported_model_apis.map((value) => ({
                value,
                label: apiLabel(value),
              })) ?? []
            }
          />
        )}
      </div>
      {original && (
        <ConnectionTest
          compact
          action={() => api.testModel(original.value.id)}
          dirty={dirty || save.isPending}
          description="May consume quota or incur cost."
        />
      )}
    </section>
  );
  return (
    <form
      className={`${modelStyles.modelForm} ${original ? modelStyles.editForm : ""}`}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      {original && (
        <>
          <div className={modelStyles.modelIdentity}>
            <div className={modelStyles.modelMark}>
              <ModelIcon
                upstream={upstream}
                provider={selectedProvider?.type}
              />
            </div>
            <div className={modelStyles.modelHeading}>
              <h3>{original.value.name}</h3>
              <span>{original.value.key}</span>
            </div>
            <div className={modelStyles.modelStatus}>
              <Switch
                id="model-enabled"
                aria-labelledby="model-enabled-label"
                checked={enabled}
                onCheckedChange={setEnabled}
              />
              <label id="model-enabled-label" htmlFor="model-enabled">
                {t(enabled ? "Enabled" : "Disabled")}
              </label>
            </div>
          </div>
          {identityFields}
        </>
      )}
      {original ? (
        <DisclosureSection
          title={t("Connection")}
          summary={apiLabel(callingApi)}
        >
          {connectionFields}
        </DisclosureSection>
      ) : (
        connectionFields
      )}
      {!original && identityFields}

      <section className={modelStyles.defaultsFields}>
        <ModelParameters
          text={settingsText}
          onChange={(next) => {
            setSettingsText(next);
            setParameterError(undefined);
          }}
          error={parameterError}
          schema={
            metadata?.settings_schema ??
            catalog.data?.settings_schemas[callingApi]
          }
          support={metadata?.parameter_support}
        />
      </section>
      <ErrorNotice
        error={parameterError ? undefined : save.error}
        retry={original ? () => void reload() : undefined}
      />
      <FormActions
        onCancel={close}
        pending={save.isPending}
        disabled={
          !upstream.trim() ||
          !name.trim() ||
          !key.trim() ||
          (!!original && !dirty)
        }
        label={t(original ? "Save changes" : "Add model")}
      />
    </form>
  );
}
