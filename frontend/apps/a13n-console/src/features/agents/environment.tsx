import {
  CursorClickIcon,
  HourglassMediumIcon,
  LightningIcon,
} from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import {
  ChoiceField,
  SettingsRow,
  Tooltip,
  TooltipPopup,
  TooltipTrigger,
} from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { OptionsRefresh } from "../../shared/forms/options-refresh";
import { Section } from "../../shared/page";
import { environmentApi, environmentTemplates } from "../environments/api";
import { useEnvironmentTypes } from "../environments/providers";
import type { AgentConfig, EnvironmentPreparation } from "./configuration";
import styles from "./agents.module.css";
import { AgentSessionConfiguration } from "./session-configuration";

const preparationOptions = [
  {
    value: "on_demand",
    icon: CursorClickIcon,
    title: "On demand",
    description: "Prepare when a tool first uses the environment.",
  },
  {
    value: "warmup",
    icon: LightningIcon,
    title: "Warm up in parallel",
    description: "Prepare the environment while inference runs.",
  },
  {
    value: "eager",
    icon: HourglassMediumIcon,
    title: "Wait before inference",
    description:
      "Wait until the environment is ready before starting inference.",
  },
] as const;

export function AgentEnvironment({
  value,
  onChange,
  environmentId,
  onEnvironmentChange,
  disabled,
  readOnly = false,
  composer = false,
  preparation,
  onPreparationChange,
  preparationReadOnly,
  sessionConfiguration,
  onSessionConfigurationChange,
  sessionInvalid = false,
}: {
  value: string | null;
  environmentId: string | null;
  onEnvironmentChange: (value: string | null) => void;
  onChange: (value: string | null) => void;
  disabled: boolean;
  readOnly?: boolean;
  composer?: boolean;
  preparation: EnvironmentPreparation;
  onPreparationChange: (value: EnvironmentPreparation) => void;
  preparationReadOnly?: boolean;
  sessionConfiguration: AgentConfig["default_session_configuration"];
  onSessionConfigurationChange: (
    value: AgentConfig["default_session_configuration"],
  ) => void;
  sessionInvalid?: boolean;
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
      (!composer || item.kind !== "external_http") &&
      !["disabled", "deleting", "deleted"].includes(item.status) &&
      !item.failure?.permanent,
  );
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
      disabled: item.enabled === false,
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
      description={
        composer
          ? t(
              "This default applies to everyone in the workspace when a thread has no primary environment. Composer can read files in the selected environment.",
            )
          : t(
              "Optional. When a session has no primary environment, use an existing environment or create one from a template. Saving the agent does not connect or start an environment.",
            )
      }
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
            group: t("Existing environment instances"),
          })),
          ...(environmentId &&
          !instances.some((item) => item.id === environmentId)
            ? [{ value: `instance:${environmentId}`, label: environmentId }]
            : []),
          ...choices.map((item) => ({
            ...item,
            group: t("Environment templates"),
          })),
          ...(value && !templates.data?.some((item) => item.id === value)
            ? [{ value, label: value }]
            : []),
        ]}
      />
      <SettingsRow
        label={t("Environment preparation")}
        stackOnNarrow={false}
        className="py-0"
      >
        <div
          className={styles.toolsetPermissions}
          role="group"
          aria-label={t("Environment preparation")}
        >
          {preparationOptions.map(
            ({ value, icon: Icon, title, description }) => (
              <Tooltip key={value}>
                <TooltipTrigger
                  render={
                    <button
                      type="button"
                      disabled={disabled || readOnly || preparationReadOnly}
                      className={styles.toolsetPermission}
                      aria-label={t(title)}
                      aria-pressed={preparation === value}
                      onClick={() => onPreparationChange(value)}
                    />
                  }
                >
                  <Icon size={15} aria-hidden="true" />
                </TooltipTrigger>
                <TooltipPopup>
                  <span className={styles.toolsetPermissionTip}>
                    <strong>{t(title)}</strong>
                    {t(description)}
                  </span>
                </TooltipPopup>
              </Tooltip>
            ),
          )}
        </div>
      </SettingsRow>
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
      <AgentSessionConfiguration
        value={sessionConfiguration}
        onChange={onSessionConfigurationChange}
        readOnly={composer || readOnly || disabled}
        invalid={sessionInvalid}
      />
    </Section>
  );
}
