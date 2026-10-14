import { useQuery } from "@tanstack/react-query";
import { Label, SearchPicker, Switch } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";

type Settings = Schema["SessionMemorySettings"];
export const defaultSessionMemory: Settings = {
  enabled: false,
  auto_organize: false,
  model: null,
};

export function SessionMemoryFields({
  value,
  onChange,
  disabled = false,
}: {
  value: Settings;
  onChange: (value: Settings) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace } = useWorkspace();
  const models = useQuery({
    queryKey: ["session-memory-models", workspace.id],
    enabled: !!value.auto_organize,
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client
          .workspace(workspace.id)
          .GET("/api/v1/models", {
            params: { query: { cursor } },
            signal,
          })
          .then(data),
      ),
  });
  return (
    <div className="grid gap-4">
      <div className="grid gap-1">
        <Label className="flex items-center gap-2">
          <Switch
            checked={!!value.enabled}
            disabled={disabled}
            onCheckedChange={(enabled) => onChange({ ...value, enabled })}
          />
          {t("Session memory")}
        </Label>
        <p className="text-muted-foreground text-xs">
          {t(
            "Share saved facts across this session's threads. Disabling retains files and history.",
          )}
        </p>
      </div>
      <div className="grid gap-1">
        <Label className="flex items-center gap-2">
          <Switch
            checked={!!value.auto_organize}
            disabled={disabled || !value.enabled}
            onCheckedChange={(auto_organize) =>
              onChange({ ...value, auto_organize })
            }
          />
          {t("Automatically organize saved memory")}
        </Label>
        <p className="text-muted-foreground text-xs">
          {t(
            "Uses the selected model after runs finish, only when memory has changed. Already accepted organization runs continue.",
          )}
        </p>
      </div>
      {value.auto_organize && (
        <>
          <ErrorNotice
            error={models.error}
            retry={() => void models.refetch()}
          />
          <SearchPicker
            label={t("Organization model")}
            placeholder={t("Choose a model")}
            emptyMessage={t("No matching models")}
            value={value.model ?? undefined}
            disabled={disabled}
            onValueChange={(model) => onChange({ ...value, model })}
            groups={[
              {
                label: t("Models"),
                options: (models.data ?? [])
                  .filter((model) => model.enabled)
                  .map((model) => ({ value: model.key, label: model.name })),
              },
            ]}
          />
        </>
      )}
    </div>
  );
}
