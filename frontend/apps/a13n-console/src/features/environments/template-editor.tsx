import { ResourceModalTitle } from "../../shared/resource-modal-title";
import { ResourceEditorButton } from "../../shared/resource-editor-button";
import {
  useResourceEditorState,
  type ResourceEditorControl,
} from "../../shared/resource-modal";
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
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions, TextAreaField } from "../../shared/form";
import styles from "../../shared/shared.module.css";
import { type EnvironmentScope } from "./api";
import { TemplateRecipe } from "./template-recipe";

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
    { open, setOpen, modalProps } = useResourceEditorState({
      controlledOpen,
      onClose,
      finalFocus,
    });
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
    <ModalFrame
      {...modalProps}
      trigger={
        controlledOpen === undefined ? (
          <ResourceEditorButton
            editing={!!templateId}
            createLabel="Create template"
            editLabel="Details"
          />
        ) : undefined
      }
      size={"lg"}
      title={
        templateId && query.data ? (
          <ResourceModalTitle
            name={query.data.value.name}
            id={query.data.value.id}
          />
        ) : (
          t(templateId ? "Environment template" : "Create environment template")
        )
      }
      description={
        templateId && query.data
          ? t("Environment template · Version {{version}}", {
              version: query.data.value.version,
            })
          : t("Choose a provider and define the environment recipe.")
      }
      closeLabel={t("Close")}
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
            <div className={styles.stack}>
              <Tabs key={generation} defaultValue="recipe">
                <TabsList aria-label={t("Environment template")}>
                  <TabsTab value="recipe">{t("Recipe")}</TabsTab>
                  <TabsTab value={"settings"}>{t("Settings")}</TabsTab>
                </TabsList>
                <TabsPanel value="recipe">
                  <CurrentRecipe
                    template={query.data.value}
                    scope={scope}
                    editable={editable}
                    close={() => setOpen(false)}
                    reload={reload}
                  />
                </TabsPanel>
                <TabsPanel value={"settings"}>
                  {
                    <TemplateSettings
                      initial={query.data}
                      editable={editable}
                      close={() => setOpen(false)}
                      reload={reload}
                    />
                  }
                </TabsPanel>
              </Tabs>
            </div>
          )
        ))}
    </ModalFrame>
  );
}

export function CurrentRecipe({
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
    query.data && (
      <TemplateRecipe
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
      <ErrorNotice error={save.error} retry={() => void reload()} />
      {editable && <FormActions pending={save.isPending} />}
    </form>
  );
}
