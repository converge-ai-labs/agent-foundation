import { useQuery } from "@tanstack/react-query";
import { ChoiceField } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { OptionsRefresh } from "../../shared/forms/options-refresh";
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
  const { workspace, basePath } = useWorkspace();
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
      title={t("Environment")}
      titleActions={
        <OptionsRefresh
          onRefresh={() =>
            Promise.all([
              templates.refetch({ throwOnError: true }),
              providers.refetch({ throwOnError: true }),
              types.refetch({ throwOnError: true }),
            ])
          }
        />
      }
      titleLink={{ href: `${basePath}/environments`, label: t("Environments") }}
      description={t(
        "Optional. When a session has no environment, its next run creates one from this template. Saving the agent does not start one.",
      )}
    >
      <ChoiceField
        label={t("Environment")}
        hideLabel
        disabled={disabled}
        readOnly={readOnly}
        value={value ?? "none"}
        onValueChange={(selected) =>
          onChange(selected === "none" ? null : selected)
        }
        options={[
          {
            value: "none",
            label: t("None"),
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
