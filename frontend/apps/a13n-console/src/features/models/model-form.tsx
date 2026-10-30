import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { type Schema } from "../../shared/api";
import { CatalogStep } from "../../shared/dialogs";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions, jsonObject, validateSettings } from "../../shared/forms";
import { modelApi, workspaceProvidersQuery } from "./api";
import { catalogRefKey } from "./catalog-picker";
import { ModelFields, ModelStatus } from "./model-fields";
import { ModelPicker } from "./model-picker";
import {
  imageInputDraft,
  imageInputPolicy,
  imageInputErrors,
} from "./image-input";
import {
  characteristicsInput,
  characteristicsDefaults,
} from "./model-information";
import { requestDefaults } from "./model-settings";
import { priceEntry, priceTable, type PriceTable } from "./model-pricing";
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
  const [nameEdited, setNameEdited] = useState(!!original);
  const [imageInput, setImageInput] = useState(() =>
    imageInputDraft(draft.characteristics.image_input),
  );
  const [initialImageInput] = useState(imageInput);
  const [settingsJson, setSettingsJson] = useState(() =>
    JSON.stringify(requestDefaults(original?.value.config), null, 2),
  );
  const [initialSettingsJson] = useState(settingsJson);
  const [settingsExpanded, setSettingsExpanded] = useState(false);
  const [manual, setManual] = useState(false);
  const providers = useQuery({
    ...workspaceProvidersQuery(client, workspace.id),
    enabled: active,
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
  const settingsSchema = definition?.settings_schemas?.[callingApi];
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
    settingsJson !== initialSettingsJson ||
    JSON.stringify(imageInput) !== JSON.stringify(initialImageInput);
  function change<K extends keyof Draft>(key: K, value: Draft[K]) {
    if (key === "name") setNameEdited(true);
    setDraft((current) => ({ ...current, [key]: value }));
  }
  /** Catalog values seed the draft; anything the reader typed wins. */
  function chooseCatalog(item: Schema["CatalogModel"] | null) {
    setManual(!item || !channels.includes(item.ref.provider));
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
    setImageInput(imageInputDraft(item.characteristics.image_input));
    setDraft((current) => ({
      ...current,
      catalog_ref: item.ref,
      model_name: channels.includes(item.ref.provider) ? item.ref.model : "",
      model_api: channels.includes(item.ref.provider)
        ? callingApi
        : "openai.chat_completions",
      name: current.name && nameEdited ? current.name : item.name,
      characteristics: characteristicsDefaults(item.characteristics),
      pricing: priceTable(pricing),
    }));
  }
  function chooseDiscovered(item: Schema["ProviderModel"] | null) {
    setManual(!item);
    setPricingBase(null);
    setImageInput(imageInputDraft(item?.characteristics?.image_input));
    setDraft((current) => ({
      ...current,
      catalog_ref: null,
      model_name: item?.slug ?? "",
      model_api: callingApi,
      name:
        current.name && nameEdited ? current.name : item?.display_name || "",
      characteristics: characteristicsDefaults(item?.characteristics),
      pricing: null,
    }));
  }
  function chooseProvider(id: string, preferredApi = "") {
    setProvider(id);
    setSettingsJson("{}");
    setPricingBase(null);
    setImageInput(imageInputDraft());
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
      workspaceProvidersQuery(client, workspace.id).queryKey,
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
      if (settingsSchema) validateSettings(settingsSchema, settings);
      const imagePolicy = imageInputPolicy(imageInput);
      const config = {
        settings,
        model_name: draft.model_name.trim(),
        model_api: callingApi,
        characteristics: {
          ...draft.characteristics,
          ...(imagePolicy === undefined ? {} : { image_input: imagePolicy }),
        },
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
    onError: () => {
      if (Object.values(imageInputErrors(imageInput)).some(Boolean))
        setSettingsExpanded(true);
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
    chooseDiscovered,
    chooseProvider,
    acceptProvider,
    settingsJson,
    settingsSchema,
    setSettingsJson,
    settingsExpanded,
    setSettingsExpanded,
    manual,
    imageInput,
    setImageInput,
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
        <ModelPicker model={model} onSelected={() => setChanging(false)} />
      </CatalogStep>
    );
  return (
    <form
      className={`${styles.modelForm} ${styles.modelConfigurationForm}`}
      onSubmit={(event) => {
        event.preventDefault();
        model.save.mutate();
      }}
    >
      <ModelFields model={model} onChangeModel={() => setChanging(true)} />
      <ErrorNotice
        error={
          model.save.error ?? model.definitions.error ?? model.providers.error
        }
        retry={() => void reload()}
      />
      <FormActions
        leading={<ModelStatus model={model} />}
        onCancel={close}
        pending={model.save.isPending}
        disabled={model.incomplete || !model.dirty}
        label={t("Save changes")}
      />
    </form>
  );
}
