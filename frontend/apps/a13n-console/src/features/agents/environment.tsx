import { useQuery } from "@tanstack/react-query";
import { ChoiceField } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { OptionsRefresh } from "../../shared/forms/options-refresh";
import { Section } from "../../shared/page";
import { environmentApi, environmentTemplates } from "../environments/api";
import { useEnvironmentTypes } from "../environments/providers";
import { runtimeGroup } from "../environments/runtime-catalog";

export function AgentEnvironment({
  value,
  onChange,
  environmentId,
  onEnvironmentChange,
  disabled,
  readOnly = false,
}: {
  value: string | null;
  environmentId: string | null;
  onEnvironmentChange: (value: string | null) => void;
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
  const environments = useQuery({
    queryKey: ["mount-environment-options", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client
          .workspace(workspace.id)
          .GET("/api/v1/environments", {
            params: { query: { cursor } },
            signal,
          })
          .then(data),
      ),
  });
  const instances = (environments.data ?? []).filter(
    (item) =>
      !["disabled", "deleting", "deleted"].includes(item.status) &&
      !item.failure?.permanent,
  );
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
              environments.refetch({ throwOnError: true }),
              providers.refetch({ throwOnError: true }),
              types.refetch({ throwOnError: true }),
            ])
          }
        />
      }
      titleLink={{ href: `${basePath}/environments`, label: t("Environments") }}
      description={t(
        "Optional. When a session has no primary environment, use an existing environment or create one from a template. Saving the agent does not connect or start an environment.",
      )}
    >
      <ChoiceField
        label={t("Environment")}
        hideLabel
        disabled={disabled}
        readOnly={readOnly}
        value={environmentId ? `instance:${environmentId}` : (value ?? "none")}
        onValueChange={(selected) => {
          if (selected.startsWith("instance:"))
            onEnvironmentChange(selected.slice("instance:".length));
          else onChange(selected === "none" ? null : selected);
        }}
        options={[
          {
            value: "none",
            label: t("None"),
          },
          ...instances.map((item) => ({
            value: `instance:${item.id}`,
            label: item.name,
            group: t("Existing environments"),
          })),
          ...(environmentId &&
          !instances.some((item) => item.id === environmentId)
            ? [{ value: `instance:${environmentId}`, label: environmentId }]
            : []),
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
        error={environments.error}
        retry={() => void environments.refetch()}
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
