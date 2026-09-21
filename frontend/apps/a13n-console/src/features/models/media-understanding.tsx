import { SettingsSection } from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { representation, type Schema } from "../../shared/api";
import { ErrorNotice, InlineLoading } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import { MediaUnderstandingFields } from "./media-understanding-fields";

type Selection = Schema["MediaUnderstandingSelection"];

export function MediaUnderstandingDefaults() {
  const { workspace, can } = useWorkspace(),
    client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const queryKey = ["media-understanding-defaults", workspace.id];
  const path = { workspace: workspace.id };
  const query = useQuery({
    queryKey,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/media-understanding-defaults", {
          params: { path },
          signal,
        })
        .then(representation),
  });
  const [draft, setDraft] = useState<{
    etag: string;
    selection: Selection;
  } | null>(null);
  const save = useMutation({
    mutationFn: (value: NonNullable<typeof draft>) =>
      client.http
        .PUT("/api/v1/workspaces/{workspace}/media-understanding-defaults", {
          params: { path, header: { "If-Match": value.etag } },
          body: value.selection,
        })
        .then(representation),
    onSuccess: (result) => {
      cache.setQueryData(queryKey, result);
      setDraft(null);
    },
  });
  const value = draft?.selection ?? query.data?.value;
  const editable =
    can("models.manage") && !!query.data?.etag && !save.isPending;
  return (
    <SettingsSection
      title={t("Media understanding")}
      description={t("Workspace defaults")}
    >
      <ErrorNotice error={query.error ?? save.error} />
      {!value ? (
        <InlineLoading />
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (draft) save.mutate(draft);
          }}
        >
          <MediaUnderstandingFields
            value={{
              image: value.image ?? null,
              video: value.video ?? null,
              audio: value.audio ?? null,
            }}
            disabled={!editable}
            inheritedLabel={t("Not configured")}
            onChange={(selection) => {
              const etag = draft?.etag ?? query.data?.etag;
              if (!etag) return;
              save.reset();
              setDraft({ etag, selection });
            }}
          />
          {draft && (
            <FormActions
              pending={save.isPending}
              disabled={!editable}
              variant="outline"
              label={t("Save")}
              cancelLabel={t("Discard")}
              onCancel={() => {
                setDraft(null);
                save.reset();
                void query.refetch();
              }}
            />
          )}
        </form>
      )}
    </SettingsSection>
  );
}
