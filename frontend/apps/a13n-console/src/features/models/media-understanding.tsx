import { ChoiceField, SettingsRow, SettingsSection } from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, representation, type Schema } from "../../shared/api";
import { ErrorNotice, InlineLoading } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import { modelApi } from "./api";

const kinds = ["image", "video", "audio"] as const;
const labels = { image: "Image", video: "Video", audio: "Audio" };
type Selection = Schema["MediaUnderstandingSelection"];

export function MediaUnderstandingDefaults() {
  const { workspace, can } = useWorkspace(),
    client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const queryKey = ["media-understanding-defaults", workspace.id];
  const path = { workspace: workspace.id };
  const api = modelApi(client, { kind: "workspace", id: workspace.id });
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
  const models = useQuery({
    queryKey: ["models", "media-understanding", workspace.id],
    queryFn: ({ signal }) => allPages((cursor) => api.models(signal, cursor)),
  });
  const providers = useQuery({
    queryKey: ["model-providers", "media-understanding", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) => api.providers(signal, cursor)),
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
  const eligibleProviders = new Set(
    providers.data
      ?.filter((provider) => provider.enabled)
      .map((provider) => provider.id),
  );
  const editable =
    can("models.manage") && !!query.data?.etag && !save.isPending;
  return (
    <SettingsSection
      title={t("Media understanding")}
      description={t("Workspace defaults")}
    >
      <ErrorNotice
        error={query.error ?? models.error ?? providers.error ?? save.error}
      />
      {!value ? (
        <InlineLoading />
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (draft) save.mutate(draft);
          }}
        >
          {kinds.map((kind) => {
            const eligible = (models.data ?? []).filter(
              (model) =>
                model.enabled &&
                eligibleProviders.has(model.provider_id) &&
                model.declarations?.capabilities?.includes(
                  `${kind}_understanding`,
                ),
            );
            const selected = value[kind] ?? "";
            const unavailable =
              selected && !eligible.some((model) => model.key === selected);
            return (
              <SettingsRow key={kind} label={t(labels[kind])}>
                <ChoiceField
                  label={t(`${labels[kind]} understanding`)}
                  hideLabel
                  value={selected}
                  disabled={
                    !editable || models.isPending || providers.isPending
                  }
                  options={[
                    { value: "", label: t("Not configured") },
                    ...(unavailable
                      ? [
                          {
                            value: selected,
                            label: `${selected} · ${t("Unavailable")}`,
                            disabled: true,
                          },
                        ]
                      : []),
                    ...eligible.map((model) => ({
                      value: model.key,
                      label: model.name,
                    })),
                  ]}
                  onValueChange={(selected) => {
                    const etag = draft?.etag ?? query.data?.etag;
                    if (!etag) return;
                    save.reset();
                    setDraft({
                      etag,
                      selection: {
                        image: value.image ?? null,
                        video: value.video ?? null,
                        audio: value.audio ?? null,
                        [kind]: selected || null,
                      },
                    });
                  }}
                />
              </SettingsRow>
            );
          })}
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
