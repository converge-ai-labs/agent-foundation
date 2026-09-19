import { FormSection, formSectionStyles } from "../../shared/form-section";
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
import { ErrorNotice } from "../../shared/feedback";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";
import { jsonObject, validateSettings } from "../../shared/validation";
import { ProviderConfiguration } from "./provider-configuration";
import { environmentApi, type EnvironmentScope } from "./api";
import { useEnvironmentTypes } from "./providers";
import editorStyles from "./template-editor.module.css";

export function TemplateConfig({
  scope,
  template,
  revision,
  close,
  reload,
  readOnly = false,
}: {
  readOnly?: boolean;
  scope: EnvironmentScope;
  template?: Schema["EnvironmentTemplate"];
  revision?: Schema["EnvironmentTemplateRevision"];
  close: () => void;
  reload?: () => Promise<void>;
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
    [version, setVersion] = useState(
      revision?.configuration_schema_version ?? "1",
    ),
    [access, setAccess] = useState<Schema["EnvironmentAccess"]>(
      revision?.access ?? "full",
    ),
    [preparation, setPreparation] = useState<"on_run" | "on_use">(
      revision?.preparation ?? "on_run",
    );
  const [configurations, setConfigurations] = useState<Record<string, string>>({
    [`${revision?.provider_id ?? ""}:${revision?.configuration_schema_version ?? "1"}`]:
      JSON.stringify(revision?.configuration ?? {}, null, 2),
  });
  const [configurationError, setConfigurationError] = useState<string>();
  const activeImageTest = useRef<{
    controller: AbortController;
    requestId: string;
    providerId: string;
  } | null>(null);
  const configurationKey = `${providerId}:${version}`;
  const configuration = configurations[configurationKey] ?? "{}";
  function setConfiguration(value: string) {
    setConfigurations((current) => ({ ...current, [configurationKey]: value }));
    setConfigurationError(undefined);
    cancelImageTest();
  }
  const [stop, setStop] = useState(
      revision?.retention.idle.stop_after?.toString() ?? "",
    ),
    [destroy, setDestroy] = useState(
      revision?.retention.idle.delete_after?.toString() ?? "",
    );
  const definition = types.data?.items.find(
    (item) =>
      item.type ===
      providers.data?.find((provider) => provider.id === providerId)?.type,
  );
  const configurationSchema =
    definition?.template_configuration_schemas[version];
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
        configuration_schema_version: version,
        access,
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
  return (
    <form
      className={formSectionStyles.form}
      onSubmit={(event) => {
        event.preventDefault();
        if (!readOnly) save.mutate();
      }}
    >
      <ErrorNotice error={providers.error ?? types.error} />
      {!basis && (
        <FormSection>
          <div className={styles.stack}>
            <FormField className="min-w-0 w-full" label={t("Name")}>
              <Input
                readOnly={readOnly}
                required={true}
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={128}
              />
            </FormField>
            <FormField className="min-w-0 w-full" label={t("Description")}>
              <Input
                readOnly={readOnly}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                maxLength={4096}
              />
            </FormField>
          </div>
        </FormSection>
      )}
      <FormSection
        title={t("Runtime")}
        description={t("Provider, permissions, and environment configuration.")}
      >
        <div className={styles.stack}>
          <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_150px]">
            <ChoiceField
              readOnly={readOnly}
              placeholder={t("Select provider")}
              value={providerId}
              className="min-w-0"
              required
              onValueChange={(value) => {
                setProviderId(value);
                const type = providers.data?.find(
                  (provider) => provider.id === value,
                )?.type;
                const versions = types.data?.items.find(
                  (entry) => entry.type === type,
                )?.template_configuration_versions;
                setVersion(versions?.at(-1) ?? "1");
                setConfigurationError(undefined);
                cancelImageTest();
              }}
              label={t("Provider")}
              options={
                providers.data
                  ?.filter(
                    (provider) =>
                      provider.id === providerId ||
                      (provider.enabled &&
                        types.data?.items.some(
                          (type) =>
                            type.type === provider.type &&
                            type.supports_managed === true,
                        )),
                  )
                  .map((provider) => ({
                    value: provider.id,
                    label: provider.name,
                  })) ?? []
              }
            />
            <ChoiceField
              readOnly={readOnly}
              placeholder={t("Select access")}
              value={access}
              className="min-w-0"
              onValueChange={(value) => {
                if (
                  value === "full" ||
                  value === "read_only" ||
                  value === "read_write"
                )
                  setAccess(value);
              }}
              label={t("Access permissions")}
              options={[
                { value: "full", label: t("Full access") },
                { value: "read_write", label: t("Read and write") },
                { value: "read_only", label: t("Read only") },
              ]}
            />
          </div>
          {definition?.type === "direct_local" && (
            <p>
              {t(
                "Root path is a base directory. Each environment gets its own environments/<environment_id> subdirectory.",
              )}
            </p>
          )}
          {providerId && (
            <ProviderConfiguration
              key={configurationKey}
              readOnly={readOnly}
              schema={configurationSchema}
              text={configuration}
              onChange={setConfiguration}
              error={configurationError}
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
          )}
        </div>
      </FormSection>
      <FormSection>
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
              {(definition?.template_configuration_versions.length ?? 0) >
                1 && (
                <ChoiceField
                  readOnly={readOnly}
                  label={t("Configuration schema version")}
                  value={version}
                  onValueChange={setVersion}
                  options={
                    definition?.template_configuration_versions.map(
                      (value) => ({
                        value,
                        label: value,
                      }),
                    ) ?? []
                  }
                />
              )}
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
        <p className={styles.muted}>
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
        <div data-a13n-form-actions className={editorStyles.footer}>
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
  );
}
