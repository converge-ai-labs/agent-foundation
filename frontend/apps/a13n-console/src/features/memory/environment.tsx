import { useQuery } from "@tanstack/react-query";
import { FormField, ReadOnlyField, SearchPicker } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";

export function MemoryEnvironmentField({
  value,
  onChange,
  readOnly,
}: {
  value: string;
  onChange: (value: string) => void;
  readOnly: boolean;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace, can } = useWorkspace();
  const allowed = can("environment.read");
  const environments = useQuery({
    queryKey: ["environments", workspace.id, "memory-choices"],
    enabled: allowed,
    staleTime: 30_000,
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/workspaces/{workspace}/environments", {
            params: {
              path: { workspace: workspace.id },
              query: { cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  const options = allowed ? (environments.data ?? []) : [];
  const selected = options.find((item) => item.id === value);
  const label = t("Memory environment");
  const current = t("Current run environment");
  const description = !allowed
    ? t(
        "You do not have permission to browse environments. The saved selection is preserved.",
      )
    : environments.isPending
      ? t("Loading environments…")
      : t(
          "Use the current run environment, or choose one that will be attached to the conversation. Selecting it here does not attach it automatically.",
        );
  return (
    <div>
      {readOnly ? (
        <ReadOnlyField label={label}>
          {selected?.name ?? (value || current)}
        </ReadOnlyField>
      ) : (
        <FormField label={label} description={description}>
          <SearchPicker
            label={label}
            placeholder={t("Choose an environment…")}
            emptyMessage={t("No matching environments")}
            value={value || "current"}
            onValueChange={(next) => onChange(next === "current" ? "" : next)}
            groups={[
              {
                label: t("Environment"),
                options: [
                  { value: "current", label: current },
                  ...options
                    .filter(
                      (item) => item.status !== "deleted" || item.id === value,
                    )
                    .map((item) => ({
                      value: item.id,
                      label: item.name,
                      description: item.id,
                      badge: t(`state.${item.status}`),
                      disabled:
                        item.status === "deleted" ||
                        item.status === "unavailable",
                    })),
                  ...(value && !selected
                    ? [
                        {
                          value,
                          label: value,
                          badge: t("Saved environment"),
                          disabled: true,
                        },
                      ]
                    : []),
                ],
              },
            ]}
            footer={
              allowed && environments.isSuccess && options.length === 0
                ? t("No environments yet. Use the current run environment.")
                : undefined
            }
          />
        </FormField>
      )}
      <ErrorNotice
        error={environments.error}
        retry={() => void environments.refetch()}
      />
    </div>
  );
}
