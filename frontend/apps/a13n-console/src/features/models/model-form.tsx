import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  SettingsRow,
  SettingsSection,
  Switch,
} from "a13n-ui";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, type Schema } from "../../shared/api";
import { CatalogStep } from "../../shared/dialogs";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions, TextAreaField, jsonObject } from "../../shared/forms";
import { IconTile } from "../../shared/identity";
import sharedStyles from "../../shared/shared.module.css";
import { ManageProvidersLink } from "../providers";
import { modelApi } from "./api";
import { CatalogPicker, catalogRefKey } from "./catalog-picker";
import { ModelIcon } from "./model-icon";
import { ModelInformation, characteristicsInput } from "./model-information";
import { defaultModelKey, keyPattern } from "./model-options";
import {
  ModelPricing,
  priceEntry,
  priceTable,
  type PriceTable,
} from "./model-pricing";
import { useModelProviderDefinitions } from "./provider-definitions";
import styles from "./models.module.css";

type Draft = {
  name: string;
  key: string;
  description: string;
  model_name: string;
  catalog_ref: Schema["CatalogRef"] | null;
  model_api: string;
  characteristics: Schema["HarnessModelCharacteristics-Input"];
  pricing: PriceTable | null;
  enabled: boolean;
};

/** The request defaults a model's configuration carries, as Settings JSON. */
function requestDefaults(config?: Schema["ModelConfig-Output"]) {
  return Object.fromEntries(
    (
      [
        "max_tokens",
        "temperature",
        "top_p",
        "extra_body",
        "extra_headers",
      ] as const
    ).flatMap((name) =>
      config?.[name] == null ||
      (typeof config[name] === "object" &&
        Object.keys(config[name]).length === 0)
        ? []
        : [[name, config[name]]],
    ),
  );
}

export type ModelDraft = ReturnType<typeof useModelDraft>;

/**
 * Everything a saved model needs, whether it is being added through the steps
 * of the add dialog or edited in one pass.
 */
export function useModelDraft({
  resource,
  providerId,
  active = true,
  close,
  onSaved,
}: {
  resource?: { value: Schema["Model"]; etag?: string };
  providerId?: string;
  /** Closed dialogs keep their draft without fetching providers or the catalog. */
  active?: boolean;
  close: () => void;
  onSaved?: (model: Schema["Model"]) => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient();
  const api = modelApi(client, workspace.id);
  const [original] = useState(resource);
  const [provider, setProvider] = useState(
    original?.value.provider_id ?? providerId ?? "",
  );
  // The entry the price table was read from; saved as it is unless the table changes.
  const [pricingBase, setPricingBase] = useState(
    original?.value.pricing ?? null,
  );
  const [draft, setDraft] = useState<Draft>(() => ({
    name: original?.value.name ?? "",
    key: original?.value.key ?? "",
    description: original?.value.description ?? "",
    model_name: original?.value.config.model_name ?? "",
    catalog_ref: original?.value.catalog_ref ?? null,
    model_api: original?.value.config.model_api ?? "",
    characteristics: characteristicsInput(
      original?.value.config.characteristics,
    ),
    pricing: priceTable(original?.value.pricing ?? null),
    enabled: original?.value.enabled ?? true,
  }));
  const [initial] = useState(draft);
  const [settingsJson, setSettingsJson] = useState(() =>
    JSON.stringify(requestDefaults(original?.value.config), null, 2),
  );
  const [initialSettingsJson] = useState(settingsJson);
  const [settingsExpanded, setSettingsExpanded] = useState(false);
  const providers = useQuery({
    queryKey: ["model-provider-choices", workspace.id],
    enabled: active,
    queryFn: ({ signal }) =>
      allPages((cursor) => api.providers(signal, cursor)),
  });
  const definitions = useModelProviderDefinitions();
  const catalog = useQuery({
    queryKey: ["model-catalog"],
    enabled: active,
    queryFn: ({ signal }) => api.catalog(signal),
  });
  const selectedProvider = providers.data?.find((item) => item.id === provider);
  const definition = definitions.data?.items.find(
    (item) => item.type === selectedProvider?.type,
  );
  const callingApi = draft.model_api || definition?.default_model_api || "";
  const customEndpoint = Object.entries(selectedProvider?.config ?? {}).some(
    ([key, value]) => key.endsWith("base_url") && !!value,
  );
  const channels = definition?.catalog_providers ?? [];
  const selectedEntry = catalog.data?.items.find(
    (item) =>
      draft.catalog_ref &&
      catalogRefKey(item.ref) === catalogRefKey(draft.catalog_ref),
  );
  const dirty =
    JSON.stringify(draft) !== JSON.stringify(initial) ||
    settingsJson !== initialSettingsJson;
  function change<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }
  /** Catalog values seed the draft; anything the reader typed wins. */
  function chooseCatalog(item: Schema["CatalogModel"] | null) {
    if (!item) {
      change("catalog_ref", null);
      return;
    }
    const official = catalog.data?.items.find(
      (entry) =>
        entry.identity === item.identity &&
        `${entry.ref.provider}/${entry.ref.model}` === item.identity,
    );
    const pricing = item.pricing ?? official?.pricing ?? null;
    setPricingBase(pricing);
    setDraft((current) => ({
      ...current,
      catalog_ref: item.ref,
      model_name: channels.includes(item.ref.provider) ? item.ref.model : "",
      model_api: channels.includes(item.ref.provider)
        ? callingApi
        : "openai.chat_completions",
      name: current.name || item.name,
      characteristics: characteristicsInput(item.characteristics),
      pricing: priceTable(pricing),
    }));
  }
  function chooseProvider(id: string, preferredApi = "") {
    setProvider(id);
    setSettingsJson("{}");
    setPricingBase(null);
    setDraft((current) => ({
      ...current,
      catalog_ref: null,
      model_name: "",
      model_api: preferredApi,
      characteristics: {},
      pricing: null,
    }));
  }
  function acceptProvider(item: Schema["Provider"], preferredApi?: string) {
    cache.setQueryData<Schema["Provider"][]>(
      ["model-provider-choices", workspace.id],
      (items) => [
        ...(items ?? []).filter((value) => value.id !== item.id),
        item,
      ],
    );
    chooseProvider(item.id, preferredApi);
  }
  const save = useMutation({
    mutationFn: async () => {
      if (!selectedProvider || !callingApi)
        throw new Error(t("Choose a provider and API."));
      const settings = jsonObject(settingsJson);
      const config = {
        ...settings,
        model_name: draft.model_name.trim(),
        model_api: callingApi,
        characteristics: draft.characteristics,
      };
      const identity = {
        provider: selectedProvider.type,
        model: config.model_name,
      };
      const pricing =
        JSON.stringify(draft.pricing) ===
        JSON.stringify(priceTable(pricingBase))
          ? pricingBase
          : priceEntry(draft.pricing, pricingBase, identity);
      const body = {
        name: draft.name,
        description: draft.description,
        enabled: draft.enabled,
        config,
        pricing,
        catalog_ref: draft.catalog_ref,
      };
      if (!original)
        return api.createModel({
          ...body,
          key: draft.key || null,
          provider_id: provider,
        });
      if (!original.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      return api.updateModel(original.value.key, original.etag, body);
    },
    onSuccess: (model) => {
      void cache.invalidateQueries();
      onSaved?.(model);
      close();
    },
  });
  return {
    close,
    original,
    provider,
    providers,
    definitions,
    definition,
    selectedProvider,
    catalog,
    channels,
    callingApi,
    customEndpoint,
    selectedEntry,
    draft,
    change,
    chooseCatalog,
    chooseProvider,
    acceptProvider,
    settingsJson,
    setSettingsJson,
    settingsExpanded,
    setSettingsExpanded,
    dirty,
    save,
    incomplete: !draft.model_name.trim() || !draft.name.trim() || !callingApi,
  };
}

/** Why the catalog may be incomplete, said once beside the model list. */
export function CatalogNotice({ model }: { model: ModelDraft }) {
  const { t } = useTranslation();
  const { catalog } = model;
  if (catalog.error || catalog.data?.status === "unavailable")
    return (
      <p className={styles.stepNote}>
        {t("Catalog unavailable. Enter a model ID to continue.")}
      </p>
    );
  if (catalog.data?.status === "stale")
    return (
      <p className={styles.stepNote}>
        {t("Showing the last available model catalog.")}
      </p>
    );
  return null;
}

/** The model this draft points at, and the two fields that address it. */
export function ModelSelection({
  model,
  onChange,
  actions,
}: {
  model: ModelDraft;
  onChange?: () => void;
  /** Quiet links that belong with the connection, not under the fields. */
  actions?: ReactNode;
}) {
  const { t } = useTranslation();
  const { draft, selectedEntry, definition, channels } = model;
  const compatible =
    !!selectedEntry && !channels.includes(selectedEntry.ref.provider);
  return (
    <div className={sharedStyles.stack}>
      <div className={styles.chosenRow}>
        <IconTile size={36} tone="elevated">
          <ModelIcon
            upstream={draft.model_name}
            catalogRef={draft.catalog_ref}
            provider={model.selectedProvider?.type}
            size={20}
          />
        </IconTile>
        <span className={styles.chosenCopy}>
          <strong>
            {selectedEntry?.name || draft.model_name || t("Custom model")}
          </strong>
          <small>
            {selectedEntry?.identity ??
              model.selectedProvider?.name ??
              t("Custom model")}
          </small>
        </span>
        {onChange && (
          <Button type="button" variant="outline" size="sm" onClick={onChange}>
            {t("Change")}
          </Button>
        )}
        {actions}
      </div>
      <div className={sharedStyles.twoColumns}>
        <FormField
          label={t("Upstream model")}
          description={t(
            compatible
              ? "Requires an OpenAI-compatible endpoint serving this model. Enter its upstream model ID."
              : "Sent to the provider. Change this for gateway aliases or deployment IDs.",
          )}
        >
          <Input
            required
            maxLength={256}
            value={draft.model_name}
            onChange={(event) => model.change("model_name", event.target.value)}
          />
        </FormField>
        {(definition?.model_apis?.length ?? 0) > 1 && (
          <ChoiceField
            label={t("API")}
            value={model.callingApi}
            onValueChange={(value) => model.change("model_api", value)}
            options={
              definition?.model_apis?.map((value) => ({
                value,
                label: definition.model_api_labels?.[value] ?? value,
              })) ?? []
            }
          />
        )}
      </div>
    </div>
  );
}

/** Name, description, declared capabilities, prices, and request defaults. */
export function ModelFields({ model }: { model: ModelDraft }) {
  const { t } = useTranslation();
  const { draft, selectedEntry, original } = model;
  return (
    <>
      <section className={styles.identityFields}>
        <div className={sharedStyles.twoColumns}>
          <FormField label={t("Name")}>
            <Input
              required
              maxLength={128}
              value={draft.name}
              onChange={(event) => model.change("name", event.target.value)}
            />
          </FormField>
          {original ? (
            <FormField label={t("Model key")} readOnly>
              <Input value={draft.key} readOnly />
            </FormField>
          ) : (
            <ModelKeyField model={model} />
          )}
        </div>
        <FormField label={t("Description")}>
          <Input
            maxLength={2048}
            value={draft.description}
            placeholder={t("Optional")}
            onChange={(event) =>
              model.change("description", event.target.value)
            }
          />
        </FormField>
      </section>
      <ModelInformation
        value={draft.characteristics}
        onChange={(value) => model.change("characteristics", value)}
      />
      <ModelPricing
        value={draft.pricing}
        onChange={(pricing) => model.change("pricing", pricing)}
      />
      {selectedEntry?.pricing_warning && (
        <p className={styles.stepNote}>{t(selectedEntry.pricing_warning)}</p>
      )}
      {selectedEntry &&
        draft.pricing &&
        (model.customEndpoint ||
          !model.channels.includes(selectedEntry.ref.provider)) && (
          <p className={styles.stepNote}>
            {t(
              "Catalog prices are references from the model provider. Your gateway may charge differently.",
            )}
          </p>
        )}
      <DisclosureSection
        title={t("Advanced")}
        summary={
          model.settingsJson.trim() !== "{}" ? t("Configured") : undefined
        }
        open={model.settingsExpanded}
        onOpenChange={model.setSettingsExpanded}
      >
        <TextAreaField
          label={t("Settings JSON")}
          hint={t(
            "Request defaults, including extra_body and extra_headers. Store secrets on the provider.",
          )}
          value={model.settingsJson}
          onChange={model.setSettingsJson}
          code
          rows={6}
        />
      </DisclosureSection>
    </>
  );
}

/** The key agents name the model by; the Service derives one when left empty. */
function ModelKeyField({ model }: { model: ModelDraft }) {
  const { t } = useTranslation();
  const derived = defaultModelKey(
    model.selectedProvider?.type,
    model.draft.model_name,
  );
  return (
    <FormField
      label={t("Model key")}
      description={t(
        "How agents refer to this model. Lowercase letters, numbers, hyphens and dots; it cannot change later.",
      )}
    >
      <Input
        required={!derived}
        maxLength={128}
        pattern={keyPattern}
        placeholder={derived}
        value={model.draft.key}
        onChange={(event) => model.change("key", event.target.value)}
      />
    </FormField>
  );
}

/** Whether agents can select the model. */
export function ModelStatus({ model }: { model: ModelDraft }) {
  const { t } = useTranslation();
  return (
    <SettingsSection>
      <SettingsRow
        controlId="model-enabled"
        label={t("Enabled")}
        description={t("Agents can select this model.")}
      >
        <Switch
          id="model-enabled"
          checked={model.draft.enabled}
          onCheckedChange={(value) => model.change("enabled", value)}
        />
      </SettingsRow>
    </SettingsSection>
  );
}

/** One-pass editor for a model that already exists. */
export function EditModelForm({
  resource,
  close,
  reload,
  onSaved,
}: {
  resource?: { value: Schema["Model"]; etag?: string };
  close: () => void;
  reload: () => Promise<void>;
  onSaved?: (model: Schema["Model"]) => void;
}) {
  const { t } = useTranslation();
  const model = useModelDraft({ resource, close, onSaved });
  const [changing, setChanging] = useState(false);
  if (changing)
    return (
      <CatalogStep
        backLabel={t("Back to details")}
        onBack={() => setChanging(false)}
      >
        <CatalogNotice model={model} />
        <CatalogPicker
          entries={model.catalog.data?.items ?? []}
          channels={model.channels}
          allowCompatible={model.selectedProvider?.type === "openai"}
          providerName={model.definition?.display_name}
          value={model.draft.catalog_ref}
          onSelect={(entry) => {
            model.chooseCatalog(entry);
            setChanging(false);
          }}
        />
      </CatalogStep>
    );
  return (
    <form
      className={styles.modelForm}
      onSubmit={(event) => {
        event.preventDefault();
        model.save.mutate();
      }}
    >
      <ModelSelection
        model={model}
        onChange={() => setChanging(true)}
        actions={
          model.selectedProvider && (
            <ManageProvidersLink variant="ghost" category="models" />
          )
        }
      />
      <ModelStatus model={model} />
      <ModelFields model={model} />
      <ErrorNotice
        error={
          model.save.error ?? model.definitions.error ?? model.providers.error
        }
        retry={() => void reload()}
      />
      <FormActions
        onCancel={close}
        pending={model.save.isPending}
        disabled={model.incomplete || !model.dirty}
        label={t("Save changes")}
      />
    </form>
  );
}
