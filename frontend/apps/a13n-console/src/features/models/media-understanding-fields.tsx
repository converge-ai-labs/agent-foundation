import { ChoiceField, DisclosureSection, SettingsRow } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { modelApi } from "./api";

export type MediaSelection = Schema["MediaUnderstandingSelection"];
const kinds = ["image", "video", "audio"] as const;
const labels = { image: "Image", video: "Video", audio: "Audio" };

export function MediaUnderstandingFields({
  value,
  onChange,
  inheritedLabel,
  readOnly = false,
  disabled = false,
}: {
  value: MediaSelection;
  onChange: (value: MediaSelection) => void;
  inheritedLabel: string;
  readOnly?: boolean;
  disabled?: boolean;
}) {
  const { workspace } = useWorkspace(),
    client = useClient(),
    { t } = useTranslation();
  const api = modelApi(client, { kind: "workspace", id: workspace.id });
  const models = useQuery({
    queryKey: ["models", "media-understanding", workspace.id],
    queryFn: ({ signal }) => allPages((cursor) => api.models(signal, cursor)),
  });
  const providers = useQuery({
    queryKey: ["model-providers", "media-understanding", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) => api.providers(signal, cursor)),
  });
  const eligibleProviders = new Set(
    providers.data
      ?.filter((provider) => provider.enabled)
      .map((provider) => provider.id),
  );
  return (
    <>
      <ErrorNotice error={models.error ?? providers.error} />
      {kinds.map((kind) => {
        const eligible = (models.data ?? []).filter(
          (model) =>
            model.enabled &&
            eligibleProviders.has(model.provider_id) &&
            model.declarations?.capabilities?.includes(`${kind}_understanding`),
        );
        const selected = value[kind] ?? "";
        const unavailable =
          selected && !eligible.some((model) => model.key === selected);
        return (
          <SettingsRow key={kind} label={t(labels[kind])}>
            <ChoiceField
              id={`media-understanding-${kind}`}
              label={t(`${labels[kind]} understanding`)}
              hideLabel
              value={selected}
              readOnly={readOnly}
              disabled={
                disabled ||
                models.isPending ||
                providers.isPending ||
                !!models.error ||
                !!providers.error
              }
              options={[
                { value: "", label: inheritedLabel },
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
              onValueChange={(selected) =>
                onChange({ ...value, [kind]: selected || null })
              }
            />
          </SettingsRow>
        );
      })}
    </>
  );
}

export function MediaUnderstandingOverrides({
  value,
  onChange,
  readOnly,
  scope,
  open,
  onOpenChange,
}: {
  value: MediaSelection;
  onChange: (value: MediaSelection) => void;
  readOnly?: boolean;
  scope: "agent" | "run";
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const inheritedLabel =
    scope === "agent" ? t("Workspace default") : t("Inherit");
  return (
    <DisclosureSection
      title={t("Media understanding")}
      summary={
        Object.values(value).some(Boolean) ? t("Customized") : inheritedLabel
      }
      open={open}
      onOpenChange={onOpenChange}
    >
      <p className="text-sm text-muted-foreground mb-3">
        {t(
          "Fallback models for file viewing when the active model cannot understand the media natively.",
        )}
      </p>
      <MediaUnderstandingFields
        value={value}
        onChange={onChange}
        inheritedLabel={inheritedLabel}
        readOnly={readOnly}
      />
    </DisclosureSection>
  );
}
