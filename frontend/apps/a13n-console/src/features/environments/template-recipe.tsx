import { FormSection, formSectionStyles } from "../../shared/form-section";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { allPages, data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { TextAreaField } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";
import { jsonObject } from "../../shared/validation";
import { environmentApi, type EnvironmentScope } from "./api";
import { useEnvironmentTypes } from "./providers";
import editorStyles from "./template-editor.module.css";

export function TemplateRecipe({
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
    [configuration, setConfiguration] = useState(
      JSON.stringify(revision?.configuration ?? {}, null, 2),
    ),
    [version, setVersion] = useState(
      revision?.configuration_schema_version ?? "1",
    ),
    [access, setAccess] = useState<Schema["EnvironmentAccess"]>(
      revision?.access ?? "full",
    ),
    [preparation, setPreparation] = useState<"on_run" | "on_use">(
      revision?.preparation ?? "on_run",
    );
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
  const save = useMutation({
    mutationFn: async () => {
      const recipe = {
        provider_id: providerId,
        configuration: jsonObject(configuration),
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
            body: { ...recipe, expected_version: basis.version },
          })
          .then(data);
      const body = { ...recipe, name, description: description || null };
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
        <FormSection aside title={t("General")}>
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
        aside
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
              onValueChange={setProviderId}
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
              label={t("Access ceiling")}
              options={[
                { value: "full", label: t("Full access") },
                { value: "read_write", label: t("Read and write") },
                { value: "read_only", label: t("Read only") },
              ]}
            />
          </div>
          <TextAreaField
            readOnly={readOnly}
            label={t("Environment recipe (JSON)")}
            hint={t(
              "Use the configuration accepted by this environment provider.",
            )}
            value={configuration}
            onChange={setConfiguration}
            code
            rows={5}
          />
        </div>
      </FormSection>
      <FormSection aside title={t("Lifecycle")}>
        <DisclosureSection
          className={editorStyles.advanced}
          title={t("Advanced settings")}
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
              <FormField
                className="min-w-0 w-full"
                label={t("Configuration schema version")}
              >
                <Input
                  readOnly={readOnly}
                  required={true}
                  value={version}
                  onChange={(event) => setVersion(event.target.value)}
                />
              </FormField>
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
            "New revisions apply to newly allocated environments. Existing environments keep their original recipe.",
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
