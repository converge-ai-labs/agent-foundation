import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
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
import { FormActions, JsonView, TextAreaField } from "../../shared/form";
import styles from "../../shared/shared.module.css";
import { type EnvironmentScope } from "./api";
import { TemplateRecipe } from "./template-recipe";

export function TemplateEditor({
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
    <ModalFrame
      onOpenChange={setOpen}
      trigger={
        <Button
          size={templateId ? "sm" : "default"}
          variant={templateId ? "outline" : "default"}
          type="button"
        >
          {t(templateId ? "Details" : "Create template")}
        </Button>
      }
      size={"lg"}
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
            <Tabs key={generation} defaultValue="recipe">
              <TabsList aria-label={t("Environment template")}>
                <TabsTab value={"settings"}>{t("Settings")}</TabsTab>
              </TabsList>
              <TabsPanel value={"settings"}>
                {
                  <TemplateSettings
                    initial={query.data}
                    close={() => setOpen(false)}
                    reload={reload}
                  />
                }
              </TabsPanel>
            </Tabs>
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

export function TemplateSettings({
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
      <FormField className="min-w-0 w-full" label={t("Name")}>
        <Input
          required={true}
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={128}
        />
      </FormField>
      <TextAreaField
        label={t("Description")}
        value={description}
        onChange={setDescription}
      />
      <Label className="flex items-center gap-2">
        <Switch checked={archived} onCheckedChange={setArchived} />
        {t("Archived")}
      </Label>
      <ErrorNotice error={save.error} retry={() => void reload()} />
      <FormActions pending={save.isPending} />
    </form>
  );
}
