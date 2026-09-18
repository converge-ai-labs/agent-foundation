import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FormField,
  Input,
  Label,
  ModalFrame,
  Switch,
  Tabs,
  TabsList,
  TabsPanel,
  TabsTab,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, representation, type Schema } from "../../shared/api";
import {
  BrandTitle,
  ResourceModalTitle,
  useResourceEditorState,
  type ResourceEditorControl,
} from "../../shared/dialogs";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions, TextAreaField } from "../../shared/forms";
import { ProviderIcon, ResourceEditorButton } from "../../shared/identity";
import styles from "../../shared/shared.module.css";
import { type EnvironmentScope } from "./api";
import { TemplateConfig, type ChosenProvider } from "./template-config";
import { TemplateHistory } from "./template-history";

/**
 * Templates are authored in one dialog: creation walks the provider catalog
 * into a configuration; editing opens the current revision beside its version
 * history and its settings.
 */
export function TemplateEditor({
  scope,
  templateId,
  editable = true,
  controlledOpen,
  onClose,
  finalFocus,
}: ResourceEditorControl & {
  scope: EnvironmentScope;
  templateId?: string;
  editable?: boolean;
}) {
  const client = useClient(),
    { t } = useTranslation(),
    [generation, setGeneration] = useState(0),
    [chosen, setChosen] = useState<ChosenProvider>(),
    { open, setOpen, modalProps } = useResourceEditorState({
      controlledOpen,
      onClose,
      finalFocus,
    });
  const query = useQuery({
    queryKey: ["environment-template", scope.kind, scope.id, templateId],
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
  const creating = !templateId;
  const title =
    !creating && query.data ? (
      <ResourceModalTitle
        name={query.data.value.name}
        id={query.data.value.id}
      />
    ) : creating && chosen ? (
      <BrandTitle mark={<ProviderIcon type={chosen.provider.type} />}>
        {t("New template on {{provider}}", {
          provider: chosen.provider.name,
        })}
      </BrandTitle>
    ) : (
      t(creating ? "Create environment template" : "Environment template")
    );
  const description =
    !creating && query.data
      ? t("Environment template · Version {{version}}", {
          version: query.data.value.version,
        })
      : creating && chosen
        ? t("Name the template and configure how its environments are created.")
        : t("Choose the provider that will run this environment.");
  return (
    <ModalFrame
      {...modalProps}
      trigger={
        controlledOpen === undefined ? (
          <ResourceEditorButton
            editing={!creating}
            createLabel="Create template"
            editLabel="Edit template"
          />
        ) : undefined
      }
      size={"lg"}
      placement="top"
      title={title}
      description={description}
      closeLabel={t("Close")}
    >
      {open &&
        (!creating && query.isPending ? (
          <Loading variant="form" rows={5} />
        ) : query.error ? (
          <ErrorNotice error={query.error} />
        ) : creating ? (
          <TemplateConfig
            scope={scope}
            close={() => setOpen(false)}
            onProviderChange={setChosen}
          />
        ) : (
          query.data && (
            <div className={styles.stack}>
              <Tabs key={generation} defaultValue="configuration">
                <TabsList aria-label={t("Environment template")}>
                  <TabsTab value="configuration">{t("Configuration")}</TabsTab>
                  <TabsTab value="versions">{t("Versions")}</TabsTab>
                  <TabsTab value="settings">{t("Settings")}</TabsTab>
                </TabsList>
                <TabsPanel value="configuration" keepMounted>
                  <CurrentTemplateConfig
                    template={query.data.value}
                    scope={scope}
                    editable={editable}
                    close={() => setOpen(false)}
                    reload={reload}
                  />
                </TabsPanel>
                <TabsPanel value="versions">
                  <TemplateHistory
                    template={query.data.value}
                    scope={scope}
                    editable={editable}
                    close={() => setOpen(false)}
                  />
                </TabsPanel>
                <TabsPanel value="settings" keepMounted>
                  <TemplateSettings
                    initial={query.data}
                    editable={editable}
                    close={() => setOpen(false)}
                    reload={reload}
                  />
                </TabsPanel>
              </Tabs>
            </div>
          )
        ))}
    </ModalFrame>
  );
}

export function CurrentTemplateConfig({
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
    <Loading variant="form" rows={5} />
  ) : query.error ? (
    <ErrorNotice error={query.error} />
  ) : (
    query.data && (
      <TemplateConfig
        scope={scope}
        template={template}
        revision={query.data}
        close={close}
        reload={reload}
        readOnly={!editable}
      />
    )
  );
}

export function TemplateSettings({
  initial,
  editable = true,
  close,
  reload,
}: {
  initial: ReturnType<typeof representation<Schema["EnvironmentTemplate"]>>;
  editable?: boolean;
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
        if (editable) save.mutate();
      }}
    >
      <FormField className="min-w-0 w-full" label={t("Name")}>
        <Input
          required={true}
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={128}
          readOnly={!editable}
        />
      </FormField>
      <TextAreaField
        label={t("Description")}
        value={description}
        onChange={setDescription}
        readOnly={!editable}
      />
      <Label className="flex items-center gap-2">
        <Switch
          checked={archived}
          disabled={!editable}
          onCheckedChange={setArchived}
        />
        {t("Archived")}
      </Label>
      <p className={styles.muted}>
        {t("Archived templates stay available to existing environments.")}
      </p>
      <ErrorNotice error={save.error} retry={() => void reload()} />
      {editable && (
        <FormActions
          pending={save.isPending}
          onCancel={close}
          disabled={
            name === basis.value.name &&
            description === (basis.value.description ?? "") &&
            archived === !!basis.value.archived_at
          }
        />
      )}
    </form>
  );
}
