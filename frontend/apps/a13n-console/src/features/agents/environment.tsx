import { useQuery } from "@tanstack/react-query";
import { ChoiceField } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { Section } from "../../shared/page";
import { environmentApi, environmentTemplates } from "../environments/api";
import { useEnvironmentTypes } from "../environments/providers";
import { runtimeGroup } from "../environments/runtime-catalog";

export function AgentEnvironment({
  value,
  onChange,
  disabled,
  readOnly = false,
}: {
  value: string | null;
  onChange: (value: string | null) => void;
  disabled: boolean;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace } = useWorkspace();
  const types = useEnvironmentTypes();
  const providers = useQuery({
    queryKey: ["environment-provider-options", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        environmentApi(client, workspace.id).providers(signal, cursor),
      ),
  });
  const templates = useQuery({
    queryKey: ["environment-template-choices", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        environmentTemplates(client, workspace.id, signal, cursor),
      ),
  });
  const groups = [
    { key: "cloud", label: t("Cloud hosting") },
    { key: "own", label: t("Own hosting") },
    { key: "other", label: t("Other runtimes") },
  ] as const;
  const choices = (templates.data ?? []).map((item) => {
    const provider = providers.data?.find(
      (provider) => provider.id === item.provider_id,
    );
    const type = provider?.type ?? "";
    const definition = types.data?.items.find(
      (definition) => definition.type === type,
    );
    return {
      value: item.id,
      label: item.name,
      category: runtimeGroup(type),
      hint: ["local", "direct_local"].includes(type)
        ? t("Local directory")
        : (definition?.display_name ?? provider?.type),
    };
  });
  return (
    <Section
      title={t("Default runtime environment (optional)")}
      description={t(
        "Choose a template for file access and command execution. Optional for model-only conversations; you can also specify an environment when starting a session.",
      )}
    >
      <ChoiceField
        label={t("Default runtime environment (optional)")}
        hideLabel
        description={
          value
            ? t(
                "Used when the session has no working environment. Saving the Agent does not start an environment.",
              )
            : undefined
        }
        disabled={disabled}
        readOnly={readOnly}
        value={value ?? "none"}
        onValueChange={(selected) =>
          onChange(selected === "none" ? null : selected)
        }
        options={[
          {
            value: "none",
            label: t("Do not create an environment automatically"),
          },
          ...groups.flatMap((group) =>
            choices
              .filter((item) => item.category === group.key)
              .map((item) => ({ ...item, group: group.label })),
          ),
          ...(value && !templates.data?.some((item) => item.id === value)
            ? [{ value, label: value }]
            : []),
        ]}
      />
      <ErrorNotice
        error={templates.error}
        retry={() => void templates.refetch()}
      />
      <ErrorNotice
        error={providers.error}
        retry={() => void providers.refetch()}
      />
      <ErrorNotice error={types.error} retry={() => void types.refetch()} />
    </Section>
  );
}
