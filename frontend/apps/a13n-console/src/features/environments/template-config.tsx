import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
} from "a13n-ui";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { allPages, data, type Schema } from "../../shared/api";
import { CatalogStep, CatalogTile, CatalogTiles } from "../../shared/dialogs";
import { ErrorNotice, InlineLoading } from "../../shared/feedback";
import {
  FormSection,
  formSectionStyles,
  jsonObject,
  validateSettings,
} from "../../shared/forms";
import { IconTile, ProviderIcon } from "../../shared/identity";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";
import { environmentApi, type EnvironmentScope } from "./api";
import editorStyles from "./environments.module.css";
import { ProviderConfiguration } from "./provider-configuration";
import { useEnvironmentTypes } from "./providers";

export interface ChosenProvider {
  provider: Schema["EnvironmentProviderAccount"];
  definition?: Schema["EnvironmentProviderMetadata"];
}

/**
 * One revision of a template: the provider that runs it and the configuration
 * that provider understands. Creation starts at the provider catalog; editing
 * opens on the current revision with a way back to the catalog.
 */
export function TemplateConfig({
  scope,
  template,
  revision,
  close,
  reload,
  readOnly = false,
  onProviderChange,
}: {
  readOnly?: boolean;
  scope: EnvironmentScope;
  template?: Schema["EnvironmentTemplate"];
  revision?: Schema["EnvironmentTemplateRevision"];
  close: () => void;
  reload?: () => Promise<void>;
  /** Lets the dialog carry the chosen provider in its title. */
  onProviderChange?: (chosen: ChosenProvider | undefined) => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    key = useIdempotency(),
    [basis] = useState(template),
    types = useEnvironmentTypes();
  const providers = useQuery({
    queryKey: ["environment-provider-options", scope.kind, scope.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        environmentApi(client, scope).providers(signal, cursor),
      ),
  });
  const [name, setName] = useState(""),
    [description, setDescription] = useState(""),
    [providerId, setProviderId] = useState(revision?.provider_id ?? ""),
    [choosing, setChoosing] = useState(!revision?.provider_id),
    [preparation, setPreparation] = useState<"on_run" | "on_use">(
      revision?.preparation ?? "on_run",
    );
  const [configurations, setConfigurations] = useState<Record<string, string>>({
    [revision?.provider_id ?? ""]: JSON.stringify(
      revision?.configuration ?? {},
      null,
      2,
    ),
  });
  const [configurationError, setConfigurationError] = useState<string>();
  const activeImageTest = useRef<{
    controller: AbortController;
    requestId: string;
    providerId: string;
  } | null>(null);
  const configuration = configurations[providerId] ?? "{}";
  function setConfiguration(value: string) {
    setConfigurations((current) => ({ ...current, [providerId]: value }));
    setConfigurationError(undefined);
    cancelImageTest();
  }
  const [stop, setStop] = useState(
      revision?.retention.idle.stop_after?.toString() ?? "",
    ),
    [destroy, setDestroy] = useState(
      revision?.retention.idle.delete_after?.toString() ?? "",
    );
  const provider = providers.data?.find((item) => item.id === providerId);
  const definition = types.data?.items.find(
    (item) => item.type === provider?.type,
  );
  const configurationSchema = definition?.template_configuration_schema;
  const report = useRef(onProviderChange);
  report.current = onProviderChange;
  useEffect(() => {
    report.current?.(
      choosing || !provider ? undefined : { provider, definition },
    );
  }, [choosing, provider, definition]);
  const imageTest = useMutation({
    mutationFn: async () => {
      const parsed = jsonObject(configuration);
      if (configurationSchema) validateSettings(configurationSchema, parsed);
      const controller = new AbortController();
      const requestId = `envtest_${crypto.randomUUID().replaceAll("-", "")}`;
      activeImageTest.current = { controller, requestId, providerId };
      try {
        return await client.http
          .POST("/api/v1/environment-providers/{provider_id}/test-image", {
            params: { path: { provider_id: providerId } },
            body: {
              request_id: requestId,
              configuration: parsed,
              workspace_id: scope.kind === "workspace" ? scope.id : null,
            },
            signal: controller.signal,
          })
          .then(data);
      } finally {
        if (activeImageTest.current?.controller === controller)
          activeImageTest.current = null;
      }
    },
  });
  function cancelActiveImageTest() {
    const active = activeImageTest.current;
    if (!active) return;
    activeImageTest.current = null;
    active.controller.abort();
    void client.http
      .POST(
        "/api/v1/environment-providers/{provider_id}/test-image/{request_id}/cancel",
        {
          params: {
            path: {
              provider_id: active.providerId,
              request_id: active.requestId,
            },
          },
          body: { workspace_id: scope.kind === "workspace" ? scope.id : null },
        },
      )
      .catch(() => undefined);
  }
  function cancelImageTest() {
    cancelActiveImageTest();
    imageTest.reset();
  }
  useEffect(() => () => cancelActiveImageTest(), []);
  function chooseProvider(id: string) {
    setProviderId(id);
    setConfigurationError(undefined);
    cancelImageTest();
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
        configuration: parsedConfiguration,
        preparation,
        retention: {
          idle: {
            stop_after: stop === "" ? null : Number(stop),
            delete_after: destroy === "" ? null : Number(destroy),
          },
        },
      };
      if (basis)
        return client.http
          .POST("/api/v1/environment-templates/{template_id}/revisions", {
            params: { path: { template_id: basis.id } },
            body: { ...templateConfig, expected_version: basis.version },
          })
          .then(data);
      const body = {
        ...templateConfig,
        name,
        description: description || null,
      };
      return environmentApi(client, scope).createTemplate(
        body,
        key.forBody(body),
      );
    },
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["environment-templates"] });
      void cache.invalidateQueries({
        queryKey: ["environment-template-history"],
      });
      close();
    },
  });
  const loadError = providers.error ?? types.error;
  if (choosing)
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
        className={formSectionStyles.form}
        onSubmit={(event) => {
          event.preventDefault();
          if (!readOnly) save.mutate();
        }}
      >
        <ErrorNotice error={loadError} />
        {!basis && (
          <FormSection>
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
                maxLength={4096}
              />
            </FormField>
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
        <ProviderConfiguration
          key={providerId}
          readOnly={readOnly}
          schema={configurationSchema}
          text={configuration}
          onChange={setConfiguration}
          error={configurationError}
          note={
            definition?.type === "direct_local"
              ? t(
                  "Root path is a base directory. Each environment gets its own environments/<environment_id> subdirectory.",
                )
              : undefined
          }
          variant={definition?.type === "docker" ? "docker" : "default"}
          imageTest={
            definition?.type === "docker" && !readOnly
              ? {
                  run: () => imageTest.mutate(),
                  pending: imageTest.isPending,
                  result: imageTest.data,
                  error: imageTest.error,
                }
              : undefined
          }
        />
        <FormSection divider={false}>
          <DisclosureSection
            className={editorStyles.advanced}
            title={t("Lifecycle")}
          >
            <div className={editorStyles.advancedBody}>
              <div className={styles.twoColumns}>
                <ChoiceField
                  readOnly={readOnly}
                  placeholder={t("Select timing")}
                  value={preparation}
                  className="min-w-0"
                  onValueChange={(value) =>
                    setPreparation(value === "on_use" ? "on_use" : "on_run")
                  }
                  label={t("Prepare environment")}
                  options={[
                    { value: "on_run", label: t("When a run starts") },
                    { value: "on_use", label: t("On first use") },
                  ]}
                />
              </div>
              <div className={styles.twoColumns}>
                <FormField
                  className="min-w-0 w-full"
                  label={t("Stop after idle seconds")}
                  description={t("Leave empty to disable automatic stopping.")}
                  disabled={definition?.supports_stop === false}
                >
                  <Input
                    readOnly={readOnly}
                    type="number"
                    min={0}
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
                    min={0}
                    step={1}
                    value={destroy}
                    onChange={(event) => setDestroy(event.target.value)}
                  />
                </FormField>
              </div>
            </div>
          </DisclosureSection>
        </FormSection>
        {basis && !readOnly && (
          <p className={editorStyles.revisionNote}>
            {t(
              "New revisions apply to newly allocated environments. Existing environments keep their original template configuration.",
            )}
          </p>
        )}
        <ErrorNotice
          error={save.error}
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
            <Button type="submit" variant="default" loading={save.isPending}>
              {t(basis ? "Publish revision" : "Create template")}
            </Button>
          </div>
        )}
      </form>
    </>
  );
  return basis ? (
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

/** Managed-capable providers, as brand tiles. */
function ProviderCatalog({
  providers,
  definitions,
  loading,
  onChoose,
}: {
  providers?: Schema["EnvironmentProviderAccount"][];
  definitions?: Schema["EnvironmentProviderMetadata"][];
  loading: boolean;
  onChoose: (id: string) => void;
}) {
  const { t } = useTranslation();
  const managed = (providers ?? []).filter(
    (provider) =>
      provider.enabled &&
      definitions?.some(
        (definition) =>
          definition.type === provider.type &&
          definition.supports_managed === true,
      ),
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
      {managed.length > 0
        ? managed.map((provider) => {
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

/** The provider this revision runs on, with a way back to the catalog. */
function ChosenProviderRow({
  provider,
  definition,
  pending,
  onChange,
}: {
  provider?: Schema["EnvironmentProviderAccount"];
  definition?: Schema["EnvironmentProviderMetadata"];
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
