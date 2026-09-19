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
import { allPages, type Schema } from "../../shared/api";
import { CatalogStep } from "../../shared/dialogs";
import { ErrorNotice } from "../../shared/feedback";
import {
  FormActions,
  TextAreaField,
  jsonObject,
  validateSettings,
} from "../../shared/forms";
import { IconTile } from "../../shared/identity";
import sharedStyles from "../../shared/shared.module.css";
import { ConnectionTest, ManageProvidersLink } from "../providers";
import { modelApi, type ModelScope } from "./api";
import { CatalogPicker, catalogRefKey } from "./catalog-picker";
import { ModelIcon } from "./model-icon";
import { ModelInformation } from "./model-information";
import { suggestedKey } from "./model-options";
import { ModelPricing } from "./model-pricing";
import { useModelProviderDefinitions } from "./provider-definitions";
import styles from "./models.module.css";

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

export type ModelDraft = ReturnType<typeof useModelDraft>;

/**
 * Everything a saved model needs, whether it is being added through the steps
 * of the add dialog or edited in one pass.
 */
export function useModelDraft({
  scope,
  resource,
  providerId,
  active = true,
  close,
  onSaved,
}: {
  scope: ModelScope;
  resource?: { value: Schema["Model"]; etag?: string };
  providerId?: string;
  /** Closed dialogs keep their draft without fetching providers or the catalog. */
  active?: boolean;
  close: () => void;
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
    enabled: active,
    queryFn: ({ signal }) =>
      allPages((cursor) => api.providers(signal, cursor)),
  });
  const definitions = useModelProviderDefinitions();
  const catalog = useQuery({
    queryKey: ["model-catalog", scope.kind, scope.id],
    enabled: active,
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
  const selectedEntry = catalog.data?.items?.find(
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
  function chooseProvider(id: string, preferredApi = "") {
    setProvider(id);
    setSettingsJson("{}");
    setDraft((current) => ({
      ...current,
      catalog_ref: null,
      upstream_model: "",
      model_api: preferredApi,
      declarations: {},
    }));
  }
  function acceptProvider(
    item: Schema["ModelProvider"],
    preferredApi?: string,
  ) {
    cache.setQueryData<Schema["ModelProvider"][]>(
      ["model-provider-choices", scope.kind, scope.id],
      (items) => [
        ...(items ?? []).filter((value) => value.id !== item.id),
        item,
      ],
    );
    chooseProvider(item.id, preferredApi);
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
  return {
    api,
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
    incomplete:
      !draft.upstream_model.trim() ||
      !draft.name.trim() ||
      !draft.key.trim() ||
      !callingApi,
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
            upstream={draft.upstream_model}
            catalogRef={draft.catalog_ref}
            provider={model.selectedProvider?.type}
            size={20}
          />
        </IconTile>
        <span className={styles.chosenCopy}>
          <strong>
            {selectedEntry?.name || draft.upstream_model || t("Custom model")}
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
            value={draft.upstream_model}
            onChange={(event) =>
              model.change("upstream_model", event.target.value)
            }
          />
        </FormField>
        {(definition?.supported_model_apis.length ?? 0) > 1 && (
          <ChoiceField
            label={t("API")}
            value={model.callingApi}
            onValueChange={(value) => model.change("model_api", value)}
            options={
              definition?.supported_model_apis.map((value) => ({
                value,
                label: definition.model_api_labels[value] ?? value,
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
          <FormField label={t("Model key")} readOnly={!!original}>
            <Input
              required
              maxLength={128}
              value={draft.key}
              onChange={(event) => model.change("key", event.target.value)}
            />
          </FormField>
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
        value={draft.declarations}
        onChange={(value) => model.change("declarations", value)}
      />
      <ModelPricing
        value={draft.declarations.pricing ?? null}
        onChange={(pricing) =>
          model.change("declarations", { ...draft.declarations, pricing })
        }
      />
      {selectedEntry?.pricing_warning && (
        <p className={styles.stepNote}>{t(selectedEntry.pricing_warning)}</p>
      )}
      {selectedEntry &&
        draft.declarations.pricing &&
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
            "Model request defaults. Agent and Run settings can override them.",
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

/** Availability and the connection check, grouped on one surface. */
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
      {model.original && (
        <ConnectionTest
          action={() => model.api.testModel(model.original!.value.id)}
          dirty={model.dirty || model.save.isPending}
          description="May consume quota or incur cost."
        />
      )}
    </SettingsSection>
  );
}

/** One-pass editor for a model that already exists. */
export function EditModelForm({
  scope,
  resource,
  close,
  reload,
  onSaved,
}: {
  scope: ModelScope;
  resource?: { value: Schema["Model"]; etag?: string };
  close: () => void;
  reload: () => Promise<void>;
  onSaved?: (model: Schema["Model"]) => void;
}) {
  const { t } = useTranslation();
  const model = useModelDraft({ scope, resource, close, onSaved });
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
            <ManageProvidersLink
              variant="ghost"
              category="models"
              scope={
                model.selectedProvider.workspace_id
                  ? "workspace"
                  : "organization"
              }
            />
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
