import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormField, Input } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { ifMatch, representation, type Schema } from "../../../shared/api";
import { Confirm, ConflictNotice } from "../../../shared/dialogs";
import { ErrorNotice } from "../../../shared/feedback";
import { TextAreaField } from "../../../shared/forms";
import { SaveBar, Section } from "../../../shared/page";
import { invalidateMemories, isStale, memoryKeys, memoryQueries } from "../api";
import { AlwaysLoadField, GuideField, LabelsField } from "../fields";
import {
  labelsError,
  memoryDraft,
  memoryUpdate,
  rebaseDraft,
  sameDraft,
  type MemoryDraft,
} from "../form";

type Resource = { value: Schema["Memory"]; etag?: string };

/**
 * What the memory is, the guide runs receive with it, and the files that lead
 * its context. Changes apply to attempts that start afterwards. A draft keeps
 * the version it started from, so a change saved meanwhile fails the save
 * instead of being overwritten.
 */
export function MemoryConfiguration({ resource }: { resource: Resource }) {
  const client = useClient(),
    { workspace, can, basePath } = useWorkspace(),
    cache = useQueryClient(),
    navigate = useNavigate(),
    { t } = useTranslation();
  const queries = memoryQueries(client, workspace.id);
  const [edit, setEdit] = useState<{ base: Resource; draft: MemoryDraft }>();
  const base = edit?.base ?? resource;
  const memory = base.value;
  const draft = edit?.draft ?? memoryDraft(memory);
  const readOnly = !can("write");
  const files = useQuery(queries.files(memory.id));
  function change(next: Partial<MemoryDraft>) {
    setEdit({ base, draft: { ...draft, ...next } });
  }
  const save = useMutation({
    mutationFn: () =>
      client.http
        .PATCH("/api/v1/workspaces/{workspace_id}/memories/{memory_id}", {
          params: {
            path: { workspace_id: workspace.id, memory_id: memory.id },
          },
          headers: ifMatch(base.etag),
          body: memoryUpdate(memory, draft),
        })
        .then(representation),
    onSuccess: (saved) => {
      cache.setQueryData(memoryKeys(workspace.id).memory(memory.id), saved);
      void invalidateMemories(cache, workspace.id);
      setEdit(undefined);
    },
  });
  const reload = useMutation({
    mutationFn: () =>
      cache.fetchQuery({ ...queries.memory(memory.id), staleTime: 0 }),
    onSuccess: (current) => {
      save.reset();
      setEdit({
        base: current,
        draft: rebaseDraft(
          memoryDraft(memory),
          draft,
          memoryDraft(current.value),
        ),
      });
    },
  });
  const dirty = !!edit && !sameDraft(draft, memoryDraft(memory));
  const labels = labelsError(draft.labels);
  return (
    <form
      className="grid gap-9"
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
              label={t("Description")}
              rows={2}
              readOnly={readOnly}
              value={draft.description}
              onChange={(description) => change({ description })}
            />
            <LabelsField
              readOnly={readOnly}
              value={draft.labels}
              onChange={(value) => change({ labels: value })}
            />
          </div>
        </Section>
        <Section
          title={t("Guide")}
          description={t(
            "What belongs in this memory and how to organize it. Runs that start after a change receive the new guide.",
          )}
        >
          <GuideField
            readOnly={readOnly}
            value={draft.guide}
            onChange={(guide) => change({ guide })}
            inherited={memory.inherited_guide}
          />
        </Section>
        <Section
          title={t("Always loaded files")}
          description={t(
            "Their full content leads the memory's context in every run. Only people who may configure the memory choose them, so an agent cannot pin its own writes.",
          )}
        >
          <AlwaysLoadField
            readOnly={readOnly}
            value={draft.alwaysLoad}
            files={files.data?.map((file) => file.path)}
            onChange={(alwaysLoad) => change({ alwaysLoad })}
          />
        </Section>
      </fieldset>
      {isStale(save.error) ? (
        <ConflictNotice
          title={t("This memory changed")}
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
      {can("write") && (
        <Section
          title={t("Delete memory")}
          description={t(
            "Deletes its files, history and thread mounts. Runs that mounted it find it gone at their next memory call.",
          )}
        >
          <div>
            <Confirm
              danger
              trigger={t("Delete memory")}
              title={t("Delete memory")}
              subject={memory.name}
              description={t(
                "The memory, every file and all of its history are deleted. This cannot be undone.",
              )}
              action={() =>
                client.http.DELETE(
                  "/api/v1/workspaces/{workspace_id}/memories/{memory_id}",
                  {
                    params: {
                      path: {
                        workspace_id: workspace.id,
                        memory_id: memory.id,
                      },
                    },
                    headers: ifMatch(base.etag),
                  },
                )
              }
              onSuccess={() => {
                navigate(`${basePath}/memories`);
                cache.removeQueries({
                  queryKey: memoryKeys(workspace.id).memory(memory.id),
                });
                void invalidateMemories(cache, workspace.id);
              }}
            />
          </div>
        </Section>
      )}
      {dirty && !readOnly && (
        <SaveBar
          title={t("Unsaved changes")}
          consequence={t(
            "Runs that start after saving use the new configuration.",
          )}
          onDiscard={() => {
            save.reset();
            setEdit(undefined);
          }}
          saveLabel={t("Save changes")}
          pending={save.isPending}
          disabled={!draft.name.trim() || !!labels || isStale(save.error)}
        />
      )}
    </form>
  );
}
