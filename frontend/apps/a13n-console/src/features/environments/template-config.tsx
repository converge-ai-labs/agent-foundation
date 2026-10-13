import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, DisclosureSection, FormField, Input } from "a13n-ui";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ApiError } from "../../service-client";
import {
  allPages,
  data,
  ifMatch,
  type representation,
  type Schema,
} from "../../shared/api";
import { CatalogStep, CatalogTile, CatalogTiles } from "../../shared/dialogs";
import { ErrorNotice, InlineLoading } from "../../shared/feedback";
import {
  FormSection,
  formSectionStyles,
  jsonObject,
  validateSettings,
} from "../../shared/forms";
import { IconTile, ProviderIcon } from "../../shared/identity";
import styles from "../../shared/shared.module.css";
import { environmentApi } from "./api";
import editorStyles from "./environments.module.css";
import { ProviderConfiguration } from "./provider-configuration";
import { useEnvironmentTypes } from "./providers";
import { EnvironmentRuntimeCatalog } from "./runtime-catalog";
import { TemplateConnection } from "./template-connection";

export interface ChosenProvider {
  provider: Schema["Provider"];
  definition?: Schema["ProviderType"];
}

/**
 * A template's configuration: the provider that runs it and the recipe that
 * provider understands. Standalone creation keeps runtime and connection
 * choices beside the recipe; editing retains the current provider catalog.
 */
export function TemplateConfig({
  template,
  close,
  reload,
  readOnly = false,
  onProviderChange,
  standalone = false,
}: {
  standalone?: boolean;
  readOnly?: boolean;
  template?: ReturnType<typeof representation<Schema["Template"]>>;
  close: () => void;
  reload?: () => Promise<void>;
  /** Lets the dialog carry the chosen provider in its title. */
  onProviderChange?: (chosen: ChosenProvider | undefined) => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    { workspace } = useWorkspace(),
    [basis] = useState(template),
    types = useEnvironmentTypes();
  const providers = useQuery({
    queryKey: ["environment-provider-options", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        environmentApi(client, workspace.id).providers(signal, cursor),
      ),
  });
  const saved = template?.value;
  const [name, setName] = useState(""),
    [description, setDescription] = useState(""),
    [providerId, setProviderId] = useState(saved?.provider_id ?? ""),
    [runtimeType, setRuntimeType] = useState(""),
    [choosing, setChoosing] = useState(!saved?.provider_id);
  const [connections, setConnections] = useState<Record<string, string>>({});
  const activeRuntime = useRef(runtimeType);
  activeRuntime.current = runtimeType;
  const [configurations, setConfigurations] = useState<Record<string, string>>({
    [saved?.provider_id ?? ""]: JSON.stringify(
      saved?.config.recipe ?? {},
      null,
      2,
    ),
  });
  const [configurationError, setConfigurationError] = useState<string>();
  // Connections of the same runtime share a recipe schema. Keep the draft
  // while creating or changing a connection, but never cross runtime schemas.
  const configurationKey = standalone ? runtimeType : providerId;
  const configuration = configurations[configurationKey] ?? "{}";
  function setConfiguration(value: string) {
    setConfigurations((current) => ({ ...current, [configurationKey]: value }));
    setConfigurationError(undefined);
  }
  const [stop, setStop] = useState(
      saved?.config.stop_after_seconds?.toString() ?? "",
    ),
    [destroy, setDestroy] = useState(
      saved?.config.delete_after_seconds?.toString() ?? "",
    );
  const provider = providers.data?.find((item) => item.id === providerId);
  const definition = types.data?.items.find(
    (item) => item.type === (standalone ? runtimeType : provider?.type),
  );
  function chooseRuntime(type: string) {
    save.reset();
    setRuntimeType(type);
    const available =
      providers.data?.filter((item) => item.enabled && item.type === type) ??
      [];
    const previous = available.find((item) => item.id === connections[type]);
    setProviderId(
      previous?.id ?? (available.length === 1 ? available[0]!.id : ""),
    );
    setConfigurationError(undefined);
  }
  function connectionCreated(created: Schema["Provider"]) {
    cache.setQueryData<Schema["Provider"][]>(
      ["environment-provider-options", workspace.id],
      (current = []) => [
        ...current.filter((item) => item.id !== created.id),
        created,
      ],
    );
    setConnections((current) => ({ ...current, [created.type]: created.id }));
    // A connection can finish saving after its dialog was dismissed and the
    // user picked another runtime. Keep that result without changing their choice.
    if (activeRuntime.current === created.type) chooseProvider(created.id);
  }
  const configurationSchema = definition?.environment_schema ?? undefined;
  const report = useRef(onProviderChange);
  report.current = onProviderChange;
  useEffect(() => {
    report.current?.(
      choosing || !provider ? undefined : { provider, definition },
    );
  }, [choosing, provider, definition]);
  function chooseProvider(id: string) {
    setProviderId(id);
    if (standalone)
      setConnections((current) => ({ ...current, [runtimeType]: id }));
    setConfigurationError(undefined);
    setChoosing(false);
  }
  const save = useMutation({
    mutationFn: async () => {
      let parsedConfiguration;
      try {
        parsedConfiguration = jsonObject(configuration);
        if (configurationSchema)
          validateSettings(configurationSchema, parsedConfiguration);
        setConfigurationError(undefined);
      } catch (error) {
        setConfigurationError(
          error instanceof Error ? error.message : t("Invalid configuration"),
        );
        throw error;
      }
      const templateConfig = {
        provider_id: providerId,
        config: {
          recipe: parsedConfiguration,
          stop_after_seconds: stop === "" ? null : Number(stop),
          delete_after_seconds: destroy === "" ? null : Number(destroy),
        },
      };
      if (basis)
        return client
          .workspace(basis.value.workspace_id)
          .PATCH("/api/v1/environment-templates/{template_id}", {
            params: {
              path: {
                template_id: basis.value.id,
              },
            },
            headers: ifMatch(basis.etag),
            body: templateConfig,
          })
          .then(data);
      return client
        .workspace(workspace.id)
        .POST("/api/v1/environment-templates", {
          body: {
            ...templateConfig,
            name,
            description: description || null,
          },
        })
        .then(data);
    },
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["environment-templates"] });
      close();
    },
  });
  // The Service refuses a recipe its provider will not run, such as a host
  // mount outside the operator's directories; the refusal reads beside it.
  const recipeRefusal =
    save.error instanceof ApiError &&
    save.error.code === "invalid_argument" &&
    save.error.details.field === "config.recipe"
      ? save.error.message
      : undefined;
  const loadError = providers.error ?? types.error;
  const readyToCreate =
    provider?.enabled &&
    provider.type === definition?.type &&
    !loadError &&
    !providers.isPending &&
    !types.isPending;
  if (choosing && !standalone)
    return (
      <div className={styles.stack}>
        <ErrorNotice error={loadError} />
        <ProviderCatalog
          providers={providers.data}
          definitions={types.data?.items}
          loading={providers.isPending || types.isPending}
          onChoose={chooseProvider}
        />
        {providerId && (
          <div className={editorStyles.editorFooter}>
            <Button
              type="button"
              variant="outline"
              onClick={() => setChoosing(false)}
            >
              {t("Cancel")}
            </Button>
          </div>
        )}
      </div>
    );
  const body = (
    <>
      <form
        className={`${formSectionStyles.form} ${standalone ? editorStyles.creationForm : ""}`}
        onSubmit={(event) => {
          event.preventDefault();
          if (standalone && !readyToCreate) return;
          if (!readOnly) save.mutate();
        }}
      >
        <ErrorNotice error={loadError} />
        <fieldset
          disabled={save.isPending}
          className={editorStyles.templateFields}
        >
          {!basis && (
            <FormSection
              title={standalone ? t("General") : undefined}
              description={
                standalone
                  ? t("Name this reusable configuration for your agents.")
                  : undefined
              }
            >
              <FormField className="min-w-0 w-full" label={t("Name")}>
                <Input
                  readOnly={readOnly}
                  required={true}
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  maxLength={128}
                />
              </FormField>
              <FormField
                className="min-w-0 w-full"
                label={t("Description")}
                description={t("Shown when someone picks this template.")}
              >
                <Input
                  readOnly={readOnly}
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  maxLength={2048}
                />
              </FormField>
            </FormSection>
          )}
          {standalone && (
            <FormSection
              title={t("Environment runtime")}
              description={t(
                "Choose where new environment instances will run.",
              )}
            >
              {types.isPending ? (
                <InlineLoading width="12rem" />
              ) : (
                <EnvironmentRuntimeCatalog
                  definitions={types.data?.items ?? []}
                  selected={runtimeType}
                  onChoose={chooseRuntime}
                />
              )}
              {definition && (
                <TemplateConnection
                  key={definition.type}
                  definition={definition}
                  providers={providers.data ?? []}
                  value={providerId}
                  onChange={chooseProvider}
                  onCreated={connectionCreated}
                />
              )}
            </FormSection>
          )}
          {/* Creation carries the brand in the dialog title instead. */}
          {basis && (
            <div className={editorStyles.chosenProviderBlock}>
              <ChosenProviderRow
                provider={provider}
                definition={definition}
                pending={providers.isPending}
                onChange={readOnly ? undefined : () => setChoosing(true)}
              />
            </div>
          )}
          {(!standalone || definition) && (
            <ProviderConfiguration
              key={providerId || runtimeType}
              readOnly={readOnly}
              schema={configurationSchema}
              text={configuration}
              onChange={setConfiguration}
              error={configurationError ?? recipeRefusal}
              note={
                ["local", "direct_local"].includes(definition?.type ?? "")
                  ? t(
                      "The root is on the Service worker machine, not your browser computer. Each environment gets a separate subdirectory under this root.",
                    )
                  : undefined
              }
              variant={definition?.type === "docker" ? "docker" : "default"}
            />
          )}
          <FormSection divider={false}>
            <DisclosureSection
              className={editorStyles.advanced}
              title={t("Lifecycle")}
            >
              <div className={editorStyles.advancedBody}>
                <div className={styles.twoColumns}>
                  <FormField
                    className="min-w-0 w-full"
                    label={t("Stop after idle seconds")}
                    description={t(
                      "Leave empty to disable automatic stopping.",
                    )}
                    disabled={definition?.supports_stop === false}
                  >
                    <Input
                      readOnly={readOnly}
                      type="number"
                      min={60}
                      step={1}
                      value={stop}
                      onChange={(event) => setStop(event.target.value)}
                    />
                  </FormField>
                  <FormField
                    className="min-w-0 w-full"
                    label={t("Delete after idle seconds")}
                    description={t(
                      "Leave empty to disable automatic deletion. If both are set, deletion must be later than stopping.",
                    )}
                    disabled={definition?.supports_destroy === false}
                  >
                    <Input
                      readOnly={readOnly}
                      type="number"
                      min={60}
                      step={1}
                      value={destroy}
                      onChange={(event) => setDestroy(event.target.value)}
                    />
                  </FormField>
                </div>
              </div>
            </DisclosureSection>
          </FormSection>
        </fieldset>
        {standalone && (
          <p className={editorStyles.creationNote}>
            {t(
              "Saving creates a template, not a running environment. When an agent uses this template, a thread without a primary environment gets its own instance; later runs reuse it.",
            )}
          </p>
        )}
        <ErrorNotice
          error={recipeRefusal ? undefined : save.error}
          retry={reload ? () => void reload() : undefined}
        />
        {!readOnly && (
          <div data-a13n-form-actions className={editorStyles.editorFooter}>
            <Button
              variant="outline"
              disabled={save.isPending}
              onClick={close}
              type="button"
            >
              {t("Cancel")}
            </Button>
            <Button
              type="submit"
              variant="default"
              loading={save.isPending}
              disabled={standalone && !readyToCreate}
            >
              {t(basis ? "Save changes" : "Create template")}
            </Button>
          </div>
        )}
      </form>
    </>
  );
  return basis || standalone ? (
    body
  ) : (
    <CatalogStep
      backLabel={t("Choose a different provider")}
      onBack={readOnly ? undefined : () => setChoosing(true)}
    >
      {body}
    </CatalogStep>
  );
}

/** Enabled providers of a type this deployment offers, as brand tiles. */
function ProviderCatalog({
  providers,
  definitions,
  loading,
  onChoose,
}: {
  providers?: Schema["Provider"][];
  definitions?: Schema["ProviderType"][];
  loading: boolean;
  onChoose: (id: string) => void;
}) {
  const { t } = useTranslation();
  const offered = (providers ?? []).filter(
    (provider) =>
      provider.enabled &&
      definitions?.some((definition) => definition.type === provider.type),
  );
  return (
    <CatalogTiles
      empty={
        loading
          ? t("Loading providers…")
          : t("No environment provider can host a template yet.")
      }
      note={t(
        "Templates run on an environment provider. Add one under Providers to see it here.",
      )}
    >
      {offered.length > 0
        ? offered.map((provider) => {
            const kind = definitions?.find(
              (definition) => definition.type === provider.type,
            )?.display_name;
            return (
              <CatalogTile
                key={provider.id}
                icon={<ProviderIcon type={provider.type} />}
                name={provider.name}
                detail={kind === provider.name ? undefined : kind}
                onClick={() => onChoose(provider.id)}
              />
            );
          })
        : undefined}
    </CatalogTiles>
  );
}

/** The provider this template runs on, with a way back to the catalog. */
function ChosenProviderRow({
  provider,
  definition,
  pending,
  onChange,
}: {
  provider?: Schema["Provider"];
  definition?: Schema["ProviderType"];
  pending: boolean;
  onChange?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className={editorStyles.chosenProvider}>
      <span className={editorStyles.chosenProviderIdentity}>
        <IconTile size={32} tone="elevated">
          <ProviderIcon type={provider?.type ?? "unknown"} />
        </IconTile>
        <span className={editorStyles.chosenProviderCopy}>
          <strong>
            {provider?.name ??
              (pending ? <InlineLoading width="7rem" /> : t("Provider"))}
          </strong>
          {definition?.display_name !== provider?.name && (
            <small>
              {definition?.display_name ??
                provider?.type ??
                t("Provider unavailable")}
            </small>
          )}
        </span>
      </span>
      {onChange && (
        <Button type="button" size="sm" variant="ghost" onClick={onChange}>
          {t("Change")}
        </Button>
      )}
    </div>
  );
}
