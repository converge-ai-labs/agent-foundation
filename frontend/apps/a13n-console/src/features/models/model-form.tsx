import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  Switch,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { allPages, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions, TextAreaField } from "../../shared/forms";
import { jsonObject, validateSettings } from "../../shared/forms";
import { ProviderIcon } from "../../shared/identity";
import { ManageProvidersLink } from "../providers/manage-link";
import styles from "../../shared/shared.module.css";
import { modelApi, type ModelScope } from "./api";
import { ConnectionTest } from "./connection-test";
import { ModelInformation } from "./model-information";
import { CatalogPicker, catalogRefKey } from "./catalog-picker";
import { ProviderSetup } from "./provider-setup";
import { useModelProviderDefinitions } from "./provider-definitions";
import { suggestedKey } from "./model-options";
import modelStyles from "./models.module.css";

type Draft = {
  name: string;
  key: string;
  description: string;
  upstream_model: string;
  catalog_ref: Schema["CatalogRef"] | null;
  model_api: string;
  declarations: Schema["ModelDeclarations-Input"];
  enabled: boolean;
};

export function ModelForm({
  scope,
  resource,
  providerId,
  close,
  reload,
  onSaved,
}: {
  scope: ModelScope;
  resource?: { value: Schema["Model"]; etag?: string };
  providerId?: string;
  close: () => void;
  reload: () => Promise<void>;
  onSaved?: (model: Schema["Model"]) => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const api = modelApi(client, scope);
  const [original] = useState(resource);
  const [provider, setProvider] = useState(
    original?.value.provider_id ?? providerId ?? "",
  );
  const [choosingProvider, setChoosingProvider] = useState(!provider);
  const [draft, setDraft] = useState<Draft>(() => ({
    name: original?.value.name ?? "",
    key: original?.value.key ?? "",
    description: original?.value.description ?? "",
    upstream_model: original?.value.upstream_model ?? "",
    catalog_ref: original?.value.catalog_ref ?? null,
    model_api: original?.value.model_api ?? "",
    declarations: original?.value.declarations ?? {},
    enabled: original?.value.enabled ?? true,
  }));
  const [initial] = useState(draft);
  const [settingsJson, setSettingsJson] = useState(() =>
    JSON.stringify(original?.value.settings ?? {}, null, 2),
  );
  const [initialSettingsJson] = useState(settingsJson);
  const [settingsExpanded, setSettingsExpanded] = useState(
    Object.keys(original?.value.settings ?? {}).length > 0,
  );
  const providers = useQuery({
    queryKey: ["model-provider-choices", scope.kind, scope.id],
    queryFn: ({ signal }) =>
      allPages((cursor) => api.providers(signal, cursor)),
  });
  const definitions = useModelProviderDefinitions();
  const catalog = useQuery({
    queryKey: ["model-catalog", scope.kind, scope.id],
    queryFn: ({ signal }) => api.catalog(signal),
  });
  const selectedProvider = providers.data?.find((item) => item.id === provider);
  const definition = definitions.data?.items.find(
    (item) => item.type === selectedProvider?.type,
  );
  const callingApi = draft.model_api || definition?.default_model_api || "";
  const customEndpoint = Object.entries(
    selectedProvider?.configuration ?? {},
  ).some(([key, value]) => key.endsWith("base_url") && !!value);
  const channels = definition?.catalog_providers ?? [];
  const [pendingVariant, setPendingVariant] = useState(false);
  const catalogValue = draft.catalog_ref
    ? catalogRefKey(draft.catalog_ref)
    : "custom";
  const selectedEntry = catalog.data?.items?.find(
    (item) => catalogRefKey(item.ref) === catalogValue,
  );
  const dirty =
    JSON.stringify(draft) !== JSON.stringify(initial) ||
    settingsJson !== initialSettingsJson;
  function change<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }
  function applyCatalog(item: Schema["CatalogModel"]) {
    const official = catalog.data?.items?.find(
      (entry) =>
        entry.identity === item.identity &&
        `${entry.ref.provider}/${entry.ref.model}` === item.identity,
    );
    setDraft((current) => ({
      ...current,
      catalog_ref: item.ref,
      upstream_model: channels.includes(item.ref.provider)
        ? item.ref.model
        : "",
      model_api: channels.includes(item.ref.provider)
        ? callingApi
        : "openai.chat_completions",
      name: current.name || item.name,
      key: current.key || suggestedKey(item.ref.model),
      declarations: {
        ...item.declarations,
        pricing:
          item.declarations.pricing ?? official?.declarations.pricing ?? null,
      },
    }));
  }
  function chooseCatalog(item: Schema["CatalogModel"] | null) {
    if (!item) {
      change("catalog_ref", null);
      return;
    }
    applyCatalog(item);
  }
  const save = useMutation({
    mutationFn: async () => {
      if (!definition || !callingApi)
        throw new Error(t("Choose a provider and API."));
      const settings = jsonObject(settingsJson);
      validateSettings(definition.settings_schemas[callingApi], settings);
      const body = {
        name: draft.name,
        description: draft.description || null,
        upstream_model: draft.upstream_model.trim(),
        catalog_ref: draft.catalog_ref,
        model_api: callingApi,
        settings,
        declarations: draft.declarations,
        enabled: draft.enabled,
      };
      if (!original)
        return api.createModel({
          ...body,
          key: draft.key,
          provider_id: provider,
        });
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
  function chooseProvider(id: string, preferredApi = "") {
    setProvider(id);
    setPendingVariant(false);
    setSettingsJson("{}");
    setDraft((current) => ({
      ...current,
      catalog_ref: null,
      upstream_model: "",
      model_api: preferredApi,
      declarations: {},
    }));
    setChoosingProvider(false);
  }
  if (choosingProvider)
    return (
      <ProviderSetup
        scope={scope}
        providers={providers.data}
        definitions={definitions.data?.items}
        value={provider}
        error={providers.error ?? definitions.error}
        onCancel={close}
        onSelect={chooseProvider}
        onCreated={(item, preferredApi) => {
          cache.setQueryData<Schema["ModelProvider"][]>(
            ["model-provider-choices", scope.kind, scope.id],
            (items) => [
              ...(items ?? []).filter((value) => value.id !== item.id),
              item,
            ],
          );
          chooseProvider(item.id, preferredApi);
        }}
      />
    );
  return (
    <form
      className={modelStyles.modelForm}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <section className={modelStyles.connectionFields}>
        <div className={modelStyles.connectionSummary}>
          {selectedProvider && <ProviderIcon type={selectedProvider.type} />}
          <div>
            <strong>{selectedProvider?.name ?? t("Loading…")}</strong>
            <span>
              {String(
                selectedProvider?.configuration.base_url ??
                  selectedProvider?.type ??
                  "",
              )}
            </span>
          </div>
          {!original && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setChoosingProvider(true)}
            >
              {t("Change")}
            </Button>
          )}
        </div>
        {typeof selectedProvider?.configuration.session_affinity_header ===
          "string" && (
          <p className={styles.muted}>
            {t("Session affinity header")}:{" "}
            {selectedProvider.configuration.session_affinity_header}
          </p>
        )}
        {original && selectedProvider && (
          <ManageProvidersLink
            category="models"
            scope={selectedProvider.workspace_id ? "workspace" : "organization"}
          />
        )}
        <FormField label={t("Model")}>
          <CatalogPicker
            key={provider}
            entries={catalog.data?.items ?? []}
            channels={channels}
            allowCompatible={selectedProvider?.type === "openai"}
            value={draft.catalog_ref}
            onSelect={chooseCatalog}
            onPendingChange={setPendingVariant}
          />
        </FormField>
        {(catalog.error || catalog.data?.status === "unavailable") && (
          <p className={styles.muted}>
            {t("Catalog unavailable. Enter a model ID to continue.")}
          </p>
        )}
        {catalog.data?.status === "stale" && (
          <p className={styles.muted}>
            {t("Showing the last available model catalog.")}
          </p>
        )}
        <div className={styles.twoColumns}>
          <FormField
            label={t("Upstream model")}
            description={t(
              selectedEntry && !channels.includes(selectedEntry.ref.provider)
                ? "Requires an OpenAI-compatible endpoint serving this model. Enter its upstream model ID."
                : "Sent to the provider. Change this for gateway aliases or deployment IDs.",
            )}
          >
            <Input
              required
              maxLength={256}
              value={draft.upstream_model}
              onChange={(event) => change("upstream_model", event.target.value)}
            />
          </FormField>
          <ChoiceField
            label={t("API")}
            value={callingApi}
            onValueChange={(value) => change("model_api", value)}
            options={
              definition?.supported_model_apis.map((value) => ({
                value,
                label: definition.model_api_labels[value] ?? value,
              })) ?? []
            }
          />
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
      <section className={modelStyles.identityFields}>
        <div className={styles.twoColumns}>
          <FormField label={t("Name")}>
            <Input
              required
              maxLength={128}
              value={draft.name}
              onChange={(event) => change("name", event.target.value)}
            />
          </FormField>
          <FormField label={t("Model key")} readOnly={!!original}>
            <Input
              required
              maxLength={128}
              value={draft.key}
              onChange={(event) => change("key", event.target.value)}
            />
          </FormField>
        </div>
        <FormField label={t("Description")}>
          <Input
            maxLength={2048}
            value={draft.description}
            placeholder={t("Optional")}
            onChange={(event) => change("description", event.target.value)}
          />
        </FormField>
      </section>
      <ModelInformation
        value={draft.declarations}
        onChange={(value) => change("declarations", value)}
      />
      <DisclosureSection
        title={t("Request settings")}
        summary={settingsJson.trim() !== "{}" ? t("Configured") : undefined}
        open={settingsExpanded}
        onOpenChange={setSettingsExpanded}
      >
        <TextAreaField
          label={t("Settings JSON")}
          hint={t(
            "Model request defaults. Agent and Run settings can override them.",
          )}
          value={settingsJson}
          onChange={setSettingsJson}
          code
          rows={6}
        />
      </DisclosureSection>
      {selectedEntry?.pricing_warning && (
        <p className={styles.muted}>{t(selectedEntry.pricing_warning)}</p>
      )}
      {selectedEntry &&
        draft.declarations.pricing &&
        (customEndpoint || !channels.includes(selectedEntry.ref.provider)) && (
          <p className={styles.muted}>
            {t(
              "Catalog prices are references from the model provider. Your gateway may charge differently.",
            )}
          </p>
        )}
      <div className={modelStyles.modelStatus}>
        <Switch
          id="model-enabled"
          checked={draft.enabled}
          onCheckedChange={(value) => change("enabled", value)}
        />
        <label htmlFor="model-enabled">{t("Enabled")}</label>
      </div>
      <ErrorNotice
        error={save.error ?? definitions.error ?? providers.error}
        retry={original ? () => void reload() : undefined}
      />
      <FormActions
        onCancel={close}
        pending={save.isPending}
        disabled={
          pendingVariant ||
          !draft.upstream_model.trim() ||
          !draft.name.trim() ||
          !draft.key.trim() ||
          !callingApi ||
          (!!original && !dirty)
        }
        label={t(original ? "Save changes" : "Add model")}
      />
    </form>
  );
}
