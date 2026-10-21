import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChoiceField,
  FormField,
  Input,
  Label,
  SearchPicker,
  Switch,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ifMatch, representation, type Schema } from "../../shared/api";
import { Confirm, ConflictNotice } from "../../shared/dialogs";
import { ErrorNotice } from "../../shared/feedback";
import { TextAreaField } from "../../shared/forms";
import { SaveBar, Section } from "../../shared/page";
import { workspaceModelsQuery } from "../models/api";
import { isStale, memoryQueries } from "./api";
import { AlwaysLoadField, GuideField } from "./fields";
import {
  spaceDraft,
  spaceUpdate,
  validSpaceDraft,
  type SpaceDraft,
} from "./space-form";
import { invalidateSpaces, spaceQueries } from "./spaces-api";
import styles from "./space-selection.module.css";

export type SpaceResource = { value: Schema["MemorySpace"]; etag?: string };
export function SpaceConfiguration({ resource }: { resource: SpaceResource }) {
  const client = useClient(),
    { workspace, basePath, can } = useWorkspace(),
    cache = useQueryClient(),
    navigate = useNavigate(),
    { t } = useTranslation();
  const [edit, setEdit] = useState<{
    base: SpaceResource;
    draft: SpaceDraft;
  }>();
  const base = edit?.base ?? resource,
    space = base.value,
    draft = edit?.draft ?? spaceDraft(space);
  const readOnly = !can("write");
  const models = useQuery(workspaceModelsQuery(client, workspace.id));
  const backing = useQuery(
    memoryQueries(client, workspace.id).memory(space.memory_id),
  );
  const files = useQuery(
    memoryQueries(client, workspace.id).files(space.memory_id),
  );
  const change = (next: Partial<SpaceDraft>) =>
    setEdit({ base, draft: { ...draft, ...next } });
  const save = useMutation({
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .PATCH("/api/v1/memory-spaces/{space_id}", {
          params: { path: { space_id: space.id } },
          headers: ifMatch(base.etag),
          body: spaceUpdate(space, draft),
        })
        .then(representation),
    onSuccess: (saved) => {
      cache.setQueryData(
        spaceQueries(client, workspace.id).detail(space.id).queryKey,
        saved,
      );
      void invalidateSpaces(cache, workspace.id);
      void cache.invalidateQueries({
        queryKey: memoryQueries(client, workspace.id).memory(space.memory_id)
          .queryKey,
      });
      setEdit(undefined);
    },
  });
  const reload = useMutation({
    mutationFn: () =>
      cache.fetchQuery({
        ...spaceQueries(client, workspace.id).detail(space.id),
        staleTime: 0,
      }),
    onSuccess: (current) => {
      const before = spaceDraft(space),
        fresh = spaceDraft(current.value);
      const changes = Object.fromEntries(
        Object.entries(draft).filter(
          ([key, value]) =>
            JSON.stringify(value) !==
            JSON.stringify(before[key as keyof SpaceDraft]),
        ),
      );
      setEdit({ base: current, draft: { ...fresh, ...changes } });
      save.reset();
    },
  });
  const dirty = !!edit && Object.keys(spaceUpdate(space, draft)).length > 0;
  return (
    <form
      className="grid max-w-[680px] gap-9"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <fieldset disabled={save.isPending} className="fieldset-reset grid gap-9">
        <Section title={t("General")}>
          <div className="grid gap-4">
            <FormField label={t("Name")} readOnly={readOnly}>
              <Input
                required
                maxLength={128}
                value={draft.name}
                onChange={(event) => change({ name: event.target.value })}
              />
            </FormField>
            <TextAreaField
              label={t("Description (optional)")}
              value={draft.description}
              onChange={(description) => change({ description })}
              readOnly={readOnly}
              rows={2}
            />
            <div className={styles.participation}>
              <div>
                <Label htmlFor="space-enabled">{t("Enabled")}</Label>
                <p>
                  {t(
                    "Disabled spaces keep their files and selections, but are omitted from new runs.",
                  )}
                </p>
              </div>
              <Switch
                id="space-enabled"
                checked={draft.enabled}
                disabled={readOnly}
                onCheckedChange={(enabled) => change({ enabled })}
              />
            </div>
          </div>
        </Section>
        <Section
          title={t("Organization")}
          description={t(
            "An organizer reads all session notes and curates shared knowledge using only this space's memory tools.",
          )}
        >
          <div className="grid gap-4">
            <div className={styles.participation}>
              <div>
                <Label htmlFor="space-automatic">
                  {t("Automatic organization")}
                </Label>
                <p>
                  {t(
                    "Organize after new memory changes. Each round starts with a fresh thread.",
                  )}
                </p>
              </div>
              <Switch
                id="space-automatic"
                checked={draft.automatic}
                disabled={readOnly}
                onCheckedChange={(automatic) => change({ automatic })}
              />
            </div>
            <FormField label={t("Organizer model")}>
              <SearchPicker
                label={t("Organizer model")}
                placeholder={t("Choose a model…")}
                emptyMessage={t(
                  "No models available. Configure a provider and model first.",
                )}
                value={draft.model}
                onValueChange={(model) => change({ model })}
                disabled={readOnly}
                groups={[
                  {
                    label: t("Available models"),
                    options: (models.data ?? []).map((model) => ({
                      value: model.key,
                      label: model.name,
                      disabled: !model.enabled,
                    })),
                  },
                ]}
              />
            </FormField>
            <p className="text-muted-foreground text-xs">
              {t("A model is required for automatic and manual organization.")}
            </p>
            <div className="grid gap-4 pt-2">
              <ChoiceField
                label={t("Automatic trigger")}
                value={draft.trigger}
                readOnly={readOnly}
                options={[
                  { value: "any_change", label: t("Any saved change") },
                  {
                    value: "change_count",
                    label: t("After a number of changes"),
                  },
                ]}
                onValueChange={(trigger) =>
                  change({ trigger: trigger as SpaceDraft["trigger"] })
                }
              />
              {draft.trigger === "change_count" && (
                <FormField
                  label={t("Changes per round")}
                  readOnly={readOnly}
                  description={t(
                    "Count saved file changes from agents, Console and API. A rename counts once; organizer edits do not count.",
                  )}
                >
                  <Input
                    type="number"
                    min={1}
                    max={1000000}
                    step={1}
                    required
                    value={draft.changeCount}
                    onChange={(event) =>
                      change({ changeCount: event.target.value })
                    }
                  />
                </FormField>
              )}
              <div className="grid gap-4 sm:grid-cols-2">
                <FormField
                  label={t("Check interval (seconds)")}
                  readOnly={readOnly}
                  description={t(
                    "How often to check for eligible changes. Minimum 10 seconds.",
                  )}
                >
                  <Input
                    type="number"
                    min={10}
                    max={86400}
                    step={1}
                    required
                    value={draft.checkInterval}
                    onChange={(event) =>
                      change({ checkInterval: event.target.value })
                    }
                  />
                </FormField>
                <FormField
                  label={t("Cooldown (seconds)")}
                  readOnly={readOnly}
                  description={t(
                    "Wait after a round ends before starting another. Use 0 for no cooldown.",
                  )}
                >
                  <Input
                    type="number"
                    min={0}
                    max={604800}
                    step={1}
                    required
                    value={draft.cooldown}
                    onChange={(event) =>
                      change({ cooldown: event.target.value })
                    }
                  />
                </FormField>
              </div>
              <p className="text-muted-foreground text-xs">
                {t(
                  "Automatic organization waits for both the trigger and cooldown. Organize now bypasses these limits and the check interval.",
                )}
              </p>
            </div>
            <ErrorNotice
              error={models.error}
              retry={() => void models.refetch()}
            />
          </div>
        </Section>
        <Section
          title={t("Memory instructions")}
          description={t(
            "Tell agents what to keep in this space. Session folder boundaries are enforced separately.",
          )}
        >
          <GuideField
            kind="file"
            hideLabel
            value={draft.guide}
            onChange={(guide) => change({ guide })}
            readOnly={readOnly}
            inherited={backing.data?.value.inherited_guide}
          />
        </Section>
        <Section
          title={t("Always loaded files")}
          description={t(
            "Only files inside a run's visible folders are loaded. Shared knowledge belongs under global/.",
          )}
        >
          <AlwaysLoadField
            value={draft.alwaysLoad}
            onChange={(alwaysLoad) => change({ alwaysLoad })}
            readOnly={readOnly}
            files={files.data?.map((file) => file.path)}
          />
        </Section>
      </fieldset>
      {isStale(save.error) ? (
        <ConflictNotice
          title={t("This space changed")}
          description={t(
            "Your draft is preserved. Load the saved version, then save again.",
          )}
          recover={{
            label: t("Load current version and keep my draft"),
            pending: reload.isPending,
            onClick: () => reload.mutate(),
          }}
        />
      ) : (
        <ErrorNotice error={save.error} />
      )}
      <ErrorNotice error={reload.error} />
      {!readOnly && (
        <Section
          title={t("Delete space")}
          description={t(
            "Remove this space from sessions and agent defaults first. Active organization must finish before deletion.",
          )}
        >
          <div>
            <Confirm
              danger
              triggerVariant="outline"
              trigger={t("Delete space")}
              title={t("Delete space")}
              subject={space.name}
              description={t(
                "Deletes this space, its files and revision history. This cannot be undone.",
              )}
              action={() =>
                client
                  .workspace(workspace.id)
                  .DELETE("/api/v1/memory-spaces/{space_id}", {
                    params: { path: { space_id: space.id } },
                    headers: ifMatch(resource.etag),
                  })
              }
              onSuccess={() => {
                navigate(`${basePath}/memory-spaces`);
                void invalidateSpaces(cache, workspace.id);
              }}
            />
          </div>
        </Section>
      )}
      {dirty && !readOnly && (
        <SaveBar
          title={t("Unsaved changes")}
          consequence={t(
            "New runs use the saved configuration. Existing runs keep their accepted mounts.",
          )}
          onDiscard={() => {
            setEdit(undefined);
            save.reset();
          }}
          saveLabel={t("Save changes")}
          pending={save.isPending}
          disabled={!validSpaceDraft(draft) || isStale(save.error)}
        />
      )}
    </form>
  );
}
