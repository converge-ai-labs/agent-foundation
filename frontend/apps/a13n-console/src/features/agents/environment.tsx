import { useQuery } from "@tanstack/react-query";
import { ChoiceField } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { Section } from "../../shared/page";
import { environmentTemplates } from "../environments/api";

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
        environmentTemplates(client, workspace.id, signal, cursor),
      ),
  });
  return (
    <Section
      title={t("Default environment template")}
      description={t(
        "Automatically creates a managed environment when the session has no workspace mount. Select external targets in session options.",
      )}
    >
      <ChoiceField
        label={t("Default environment template")}
        hideLabel
        disabled={disabled}
        value={value ?? "none"}
        onValueChange={(selected) =>
          onChange(selected === "none" ? null : selected)
        }
        options={[
          { value: "none", label: t("No default environment template") },
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
    </Section>
  );
}
