import { PageActions } from "../../shared/page-actions";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Dialog, Input, SelectField, Switch, Tabs } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import { allPages, data, representation, type Schema } from "../../shared/api";
import {
  ErrorNotice,
  Empty,
  Loading,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { FormActions, JsonView, TextArea } from "../../shared/form";
import { Table, Pagination, useCursor } from "../../shared/collection";
import { jsonObject } from "../../shared/validation";
import { useIdempotency } from "../../shared/idempotency";
import { environmentApi, type EnvironmentScope } from "./api";
import { useEnvironmentTypes } from "./providers";
import styles from "../../shared/shared.module.css";
import editorStyles from "./template-editor.module.css";

export function EnvironmentTemplates({ scope }: { scope: EnvironmentScope }) {
  const client = useClient(),
    { can, organizationAdmin } = useAccess(),
    { t } = useTranslation(),
    page = useCursor();
  const query = useQuery({
    queryKey: ["environment-templates", scope.kind, scope.id, page.cursor],
    queryFn: ({ signal }) =>
      environmentApi(client, scope).templates(signal, page.cursor),
  });
  const manage =
    scope.kind === "organization"
      ? organizationAdmin
      : can("environment_template.manage");
  return (
    <div className={styles.stack}>
      <PageActions>{manage && <TemplateEditor scope={scope} />}</PageActions>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : query.data?.items.length ? (
        <>
          <Table
            items={query.data.items}
            columns={[
              {
                label: t("Name"),
                render: (item) => (
                  <>
                    <strong>{item.name}</strong>
                    <small>{item.description}</small>
                  </>
                ),
              },
              {
                label: t("Scope"),
                render: (item) =>
                  t(item.workspace_id ? "Workspace" : "Organization"),
              },
              { label: t("Version"), render: (item) => `v${item.version}` },
              {
                label: t("Status"),
                render: (item) => (
                  <StateBadge
                    state={item.archived_at ? "archived" : "active"}
                  />
                ),
              },
              {
                label: t("Actions"),
                align: "right",
                render: (item) => (
                  <TemplateEditor
                    scope={
                      item.workspace_id
                        ? { kind: "workspace", id: item.workspace_id }
                        : { kind: "organization", id: item.organization_id }
                    }
                    templateId={item.id}
                    editable={item.workspace_id ? manage : organizationAdmin}
                  />
                ),
              },
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        !query.error && (
          <Empty
            title={t("No environment templates")}
            description={t(
              "Create a recipe, then choose it when starting a conversation.",
            )}
          />
        )
      )}
    </div>
  );
}
function TemplateEditor({
  scope,
  templateId,
  editable = true,
}: {
  scope: EnvironmentScope;
  templateId?: string;
  editable?: boolean;
}) {
  const client = useClient(),
    { t } = useTranslation(),
    [open, setOpen] = useState(false),
    [generation, setGeneration] = useState(0);
  const query = useQuery({
    queryKey: ["environment-templates", scope.kind, scope.id, templateId],
    enabled: open && !!templateId,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/environment-templates/{resource_id}", {
          params: { path: { resource_id: templateId! } },
          signal,
        })
        .then(representation),
  });
  async function reload() {
    await query.refetch();
    setGeneration((value) => value + 1);
  }
  return (
    <Dialog
      size="wide"
      title={t(
        templateId ? "Environment template" : "Create environment template",
      )}
      description={t(
        templateId
          ? "New revisions apply to newly allocated environments. Existing environments keep their original recipe."
          : "Choose a provider and define the environment recipe.",
      )}
      closeLabel={t("Close")}
      open={open}
      onOpenChange={setOpen}
      trigger={
        <Button
          size={templateId ? "sm" : "md"}
          variant={templateId ? "secondary" : "primary"}
        >
          {t(templateId ? "Details" : "Create template")}
        </Button>
      }
    >
      {open &&
        (templateId && query.isPending ? (
          <Loading />
        ) : query.error ? (
          <ErrorNotice error={query.error} />
        ) : !templateId ? (
          <TemplateRecipe scope={scope} close={() => setOpen(false)} />
        ) : (
          query.data && (
            <Tabs
              key={generation}
              defaultValue="recipe"
              label={t("Environment template")}
              items={[
                {
                  value: "recipe",
                  label: t("Recipe"),
                  content: (
                    <CurrentRecipe
                      scope={scope}
                      template={query.data.value}
                      editable={editable}
                      close={() => setOpen(false)}
                      reload={reload}
                    />
                  ),
                },
                {
                  value: "history",
                  label: t("Revisions"),
                  content: (
                    <TemplateHistory
                      template={query.data.value}
                      scope={scope}
                      editable={editable}
                      close={() => setOpen(false)}
                    />
                  ),
                },
                ...(editable
                  ? [
                      {
                        value: "settings",
                        label: t("Settings"),
                        content: (
                          <TemplateSettings
                            initial={query.data}
                            close={() => setOpen(false)}
                            reload={reload}
                          />
                        ),
                      },
                    ]
                  : []),
              ]}
            />
          )
        ))}
    </Dialog>
  );
}
function CurrentRecipe({
  template,
  scope,
  editable,
  close,
  reload,
}: {
  template: Schema["EnvironmentTemplate"];
  scope: EnvironmentScope;
  editable: boolean;
  close: () => void;
  reload: () => Promise<void>;
}) {
  const client = useClient();
  const query = useQuery({
    queryKey: ["environment-revision", template.current_revision_id],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/environment-template-revisions/{revision_id}", {
          params: { path: { revision_id: template.current_revision_id } },
          signal,
        })
        .then(data),
  });
  return query.isPending ? (
    <Loading />
  ) : query.error ? (
    <ErrorNotice error={query.error} />
  ) : (
    query.data &&
    (editable ? (
      <TemplateRecipe
        scope={scope}
        template={template}
        revision={query.data}
        close={close}
        reload={reload}
      />
    ) : (
      <JsonView value={query.data} />
    ))
  );
}
function TemplateHistory({
  template,
  scope,
  editable,
  close,
}: {
  template: Schema["EnvironmentTemplate"];
  scope: EnvironmentScope;
  editable: boolean;
  close: () => void;
}) {
  const client = useClient(),
    { t } = useTranslation(),
    page = useCursor(),
    [restore, setRestore] = useState<Schema["EnvironmentTemplateRevision"]>();
  const query = useQuery({
    queryKey: ["environment-template-history", template.id, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/environment-templates/{template_id}/revisions", {
          params: {
            path: { template_id: template.id },
            query: { cursor: page.cursor },
          },
          signal,
        })
        .then(data),
  });
  if (restore)
    return (
      <>
        <Button onClick={() => setRestore(undefined)}>
          {t("Back to revisions")}
        </Button>
        <TemplateRecipe
          scope={scope}
          template={template}
          revision={restore}
          close={close}
        />
      </>
    );
  return (
    <div className={styles.stack}>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : (
        query.data && (
          <>
            <Table
              items={query.data.items}
              columns={[
                { label: t("Version"), render: (item) => `v${item.version}` },
                {
                  label: t("Created"),
                  render: (item) => <Timestamp value={item.created_at} />,
                },
                {
                  label: t("Recipe"),
                  render: (item) => (
                    <details>
                      <summary>{t("View recipe")}</summary>
                      <JsonView value={item} />
                    </details>
                  ),
                },
                {
                  label: t("Actions"),
                  align: "right",
                  render: (item) =>
                    editable &&
                    item.id !== template.current_revision_id && (
                      <Button size="sm" onClick={() => setRestore(item)}>
                        {t("Restore as new revision")}
                      </Button>
                    ),
                },
              ]}
            />
            <Pagination page={page} next={query.data.next_cursor} />
          </>
        )
      )}
    </div>
  );
}
function TemplateRecipe({
  scope,
  template,
  revision,
  close,
  reload,
}: {
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
      className={editorStyles.form}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <ErrorNotice error={providers.error ?? types.error} />
      {!basis && (
        <section className={editorStyles.section}>
          <div className={styles.twoColumns}>
            <Input
              label={t("Name")}
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
              maxLength={128}
            />
            <Input
              label={t("Description")}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              maxLength={4096}
            />
          </div>
        </section>
      )}
      <section className={editorStyles.section}>
        <div className={editorStyles.sectionHeading}>
          <h3>{t("Runtime")}</h3>
        </div>
        <div className={styles.stack}>
          <div className={styles.twoColumns}>
            <SelectField
              label={t("Provider")}
              placeholder={t("Select provider")}
              required
              value={providerId}
              onValueChange={setProviderId}
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
            <SelectField
              label={t("Access ceiling")}
              placeholder={t("Select access")}
              value={access}
              onValueChange={(value) => {
                if (
                  value === "full" ||
                  value === "read_only" ||
                  value === "read_write"
                )
                  setAccess(value);
              }}
              options={[
                { value: "full", label: t("Full access") },
                { value: "read_write", label: t("Read and write") },
                { value: "read_only", label: t("Read only") },
              ]}
            />
          </div>
          <TextArea
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
      </section>
      <details className={editorStyles.advanced}>
        <summary>{t("Lifecycle and advanced settings")}</summary>
        <div className={editorStyles.advancedBody}>
          <div className={styles.twoColumns}>
            <SelectField
              label={t("Prepare environment")}
              placeholder={t("Select timing")}
              value={preparation}
              onValueChange={(value) =>
                setPreparation(value === "on_use" ? "on_use" : "on_run")
              }
              options={[
                { value: "on_run", label: t("When a run starts") },
                { value: "on_use", label: t("On first use") },
              ]}
            />
            <Input
              label={t("Configuration schema version")}
              value={version}
              onChange={(event) => setVersion(event.target.value)}
              required
            />
          </div>
          <div className={styles.twoColumns}>
            <Input
              label={t("Stop after idle seconds")}
              hint={t("Leave empty to disable automatic stopping.")}
              type="number"
              min={0}
              step={1}
              value={stop}
              disabled={definition?.supports_stop === false}
              onChange={(event) => setStop(event.target.value)}
            />
            <Input
              label={t("Delete after idle seconds")}
              hint={t(
                "Leave empty to disable automatic deletion. If both are set, deletion must be later than stopping.",
              )}
              type="number"
              min={0}
              step={1}
              value={destroy}
              disabled={definition?.supports_destroy === false}
              onChange={(event) => setDestroy(event.target.value)}
            />
          </div>
        </div>
      </details>
      <ErrorNotice
        error={save.error}
        retry={reload ? () => void reload() : undefined}
      />
      <div data-a13n-form-actions className={editorStyles.footer}>
        <Button onClick={close} disabled={save.isPending}>
          {t("Cancel")}
        </Button>
        <Button type="submit" variant="primary" loading={save.isPending}>
          {t(basis ? "Publish revision" : "Create template")}
        </Button>
      </div>
    </form>
  );
}
function TemplateSettings({
  initial,
  close,
  reload,
}: {
  initial: ReturnType<typeof representation<Schema["EnvironmentTemplate"]>>;
  close: () => void;
  reload: () => Promise<void>;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    [basis] = useState(initial),
    [name, setName] = useState(initial.value.name),
    [description, setDescription] = useState(initial.value.description ?? ""),
    [archived, setArchived] = useState(!!initial.value.archived_at);
  const save = useMutation({
    mutationFn: () =>
      client.http
        .PATCH("/api/v1/environment-templates/{template_id}", {
          params: {
            path: { template_id: basis.value.id },
            header: { "If-Match": basis.etag ?? "" },
          },
          body: { name, description: description || null, archived },
        })
        .then(data),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["environment-templates"] });
      close();
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
      <Input
        label={t("Name")}
        value={name}
        onChange={(event) => setName(event.target.value)}
        required
        maxLength={128}
      />
      <TextArea
        label={t("Description")}
        value={description}
        onChange={setDescription}
      />
      <Switch
        label={t("Archived")}
        checked={archived}
        onCheckedChange={setArchived}
      />
      <ErrorNotice error={save.error} retry={() => void reload()} />
      <FormActions pending={save.isPending} />
    </form>
  );
}
