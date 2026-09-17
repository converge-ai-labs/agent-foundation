import { useQuery } from "@tanstack/react-query";
import { ChoiceField } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { EditorSection } from "./section";

export function AgentEnvironment({
  value,
  onChange,
  disabled,
}: {
  value: string | null;
  onChange: (value: string | null) => void;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace } = useWorkspace();
  const templates = useQuery({
    queryKey: ["environment-template-choices", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/workspaces/{workspace}/environment-templates", {
            params: {
              path: { workspace: workspace.id },
              query: { cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  return (
    <EditorSection
      title={t("Default environment")}
      description={t("Used for new sessions.")}
    >
      <ChoiceField
        label={t("Default environment")}
        hideLabel
        disabled={disabled}
        value={value ?? "none"}
        onValueChange={(selected) =>
          onChange(selected === "none" ? null : selected)
        }
        options={[
          { value: "none", label: t("No default environment") },
          ...(templates.data ?? []).map((item) => ({
            value: item.id,
            label: item.name,
          })),
          ...(value && !templates.data?.some((item) => item.id === value)
            ? [{ value, label: value }]
            : []),
        ]}
      />
      <ErrorNotice
        error={templates.error}
        retry={() => void templates.refetch()}
      />
    </EditorSection>
  );
}
