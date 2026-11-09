import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormField, Input, Label, Switch } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ifMatch, representation, type Schema } from "../../shared/api";
import { Confirm, ConflictNotice } from "../../shared/dialogs";
import { ErrorNotice } from "../../shared/feedback";
import { TextAreaField } from "../../shared/forms";
import { FormSection } from "../../shared/forms";
import { SaveBar } from "../../shared/page";
import { OrganizationPolicyFields } from "./organization-policy-fields";
import { isStale, memoryQueries } from "./api";
import { AlwaysLoadField } from "./fields";
import {
  spaceDraft,
  spaceUpdate,
  validSpaceDraft,
  type SpaceDraft,
} from "./space-form";
import { invalidateSpaces, spaceQueries } from "./spaces-api";
import styles from "./space-selection.module.css";

import { FileGuidanceField } from "./guidance-field";

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
      className="grid max-w-[680px] gap-6"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <fieldset disabled={save.isPending} className="fieldset-reset grid gap-6">
        <FormSection title={t("General")}>
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
        </FormSection>
        <FormSection
          title={t("Organization")}
          description={t(
            "An organizer reads all session notes and curates shared knowledge using only this space's memory tools.",
          )}
        >
          <OrganizationPolicyFields
            draft={draft}
            change={change}
            readOnly={readOnly}
          />
        </FormSection>
        <FormSection
          title={t("Memory instructions")}
          description={t(
            "Tell agents what to keep in this space. Session folder boundaries are enforced separately.",
          )}
        >
          <FileGuidanceField
            value={draft.guidance}
            onChange={(guidance) => change({ guidance })}
            readOnly={readOnly}
          />
        </FormSection>
        <FormSection
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
        </FormSection>
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
        <FormSection
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
        </FormSection>
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
