import { useTranslation } from "../i18n";
import { ModelIdentity } from "../shell/model-identity";
import { Button, ModalFrame } from "a13n-ui";
import { CaretRight, Desktop } from "@phosphor-icons/react";
import { useState } from "react";
import type { Schema } from "../transport/client";
import { thinkingLabel } from "./thinking-picker";
import { ModelOptions } from "./model-picker";
import {
  EnvironmentsEditor,
  invalidEnvironments,
} from "../configuration/environments";
import { environmentSummary } from "../configuration/environment-selection";
import styles from "./new-conversation.module.css";
import panelStyles from "./composer-settings.module.css";
import {
  ComposerSettings,
  SettingsChoices,
  SettingsRow,
  useComposerSettings,
} from "./composer-settings";
import { ModelControlPanel, type ModelControlProps } from "./model-controls";
import { useTaskOptionsDialog } from "./task-options";

export function RunEnvironments({
  catalog,
  configuration,
  value,
  onChange,
  disabled,
}: {
  catalog?: Schema<"ThreadSelectorCatalog">;
  configuration?:
    Schema<"ThreadConfigurationView"> | Schema<"ThreadConfiguration">;
  value?: Schema<"EnvironmentSelectionPatch">;
  onChange: (value: Schema<"EnvironmentSelectionPatch"> | undefined) => void;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState<Schema<"EnvironmentSelectionPatch">>();
  useTaskOptionsDialog(editing !== undefined);
  const roots = value?.local_roots ?? configuration?.local_roots ?? [];
  const bindings =
    value?.environment_bindings ?? configuration?.environment_bindings ?? [];
  const workingDefault =
    value?.default_environment === undefined
      ? configuration?.default_environment
      : value.default_environment;
  const summary = environmentSummary({
    local_roots: roots,
    environment_bindings: bindings,
  });
  const invalid = editing ? invalidEnvironments(editing) : false;
  const beginEditing = () =>
    setEditing({
      ...value,
      local_roots: roots,
      environment_bindings: bindings,
      default_environment: workingDefault ?? null,
    });
  const closeEditor = () => {
    setEditing(undefined);
  };
  const footer = (
    <>
      <Button
        type="button"
        variant="ghost"
        aria-label={t("Use chat defaults")}
        className={panelStyles.resetAction}
        disabled={disabled}
        onClick={() => {
          onChange(undefined);
          closeEditor();
        }}
      >
        {t("Reset to defaults")}
      </Button>
      <div className={panelStyles.confirmActions}>
        <Button type="button" variant="outline" onClick={closeEditor}>
          {t("Cancel")}
        </Button>
        <Button
          type="button"
          aria-label={t("Use for next run")}
          disabled={disabled || invalid || !editing}
          onClick={() => {
            if (!editing || disabled) return;
            onChange(editing);
            closeEditor();
          }}
        >
          {t("Apply")}
        </Button>
      </div>
    </>
  );
  const content = editing && (
    <EnvironmentsEditor
      value={editing}
      profiles={catalog?.environments}
      inheritedProfile={configuration?.environment_profile_id}
      inheritLabel={t("Use chat default")}
      onChange={(patch) => setEditing({ ...editing, ...patch })}
    />
  );
  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        disabled={disabled || !configuration}
        aria-label={t("Environments")}
        title={summary}
        className={panelStyles.optionAction}
        onClick={beginEditing}
      >
        <Desktop aria-hidden />
        <span className={panelStyles.optionCopy}>
          <span>{t("Environment")}</span>
          <small className={panelStyles.environmentLabel}>
            {bindings.length
              ? t("{{count}} remote", { count: bindings.length })
              : t("Local")}
          </small>
        </span>
        <CaretRight aria-hidden="true" size={14} />
      </Button>
      <ModalFrame
        bottomStickOnMobile
        open={editing !== undefined}
        onOpenChange={(open) => {
          if (!open) setEditing(undefined);
        }}
        title={t("Environments")}
        closeLabel={t("Close")}
        description={t("Applies to upcoming runs in this tab.")}
        footer={footer}
      >
        <fieldset className="min-w-0" disabled={disabled}>
          {content}
        </fieldset>
      </ModalFrame>
    </>
  );
}

type RunChoiceProps = ModelControlProps & {
  catalog?: Schema<"ThreadSelectorCatalog">;
  agentId: string;
  defaultAgentId?: string;
  defaultModelId?: string | null;
  modelId?: string;
  disabled: boolean;
  onAgentChange: (value: string) => void;
  onModelChange: (value: string | undefined) => void;
};

export function ThreadRunChoices(props: RunChoiceProps) {
  const { t } = useTranslation();
  const { catalog, agentId, defaultAgentId, defaultModelId, modelId } = props;
  const agent = catalog?.agents.find(
    (item) => item.agent_id === (agentId || defaultAgentId),
  );
  const inheritedModelId = defaultModelId ?? agent?.model_id;
  const selectedModelId = modelId ?? inheritedModelId;
  const model = catalog?.models?.find(
    (item) => item.model_id === selectedModelId,
  );
  const modelName =
    model?.name ??
    (selectedModelId
      ? t("{{name}} (unavailable)", { name: selectedModelId })
      : t("Model unavailable"));
  return (
    <div
      className={panelStyles.identity}
      aria-label={t("Agent: {{agent}}. Model: {{model}}", {
        agent: agent?.name ?? t("Default agent"),
        model: modelName,
      })}
      title={`${agent?.name ?? t("Default agent")} - ${modelName}`}
    >
      <ComposerSettings
        label={
          <span className={panelStyles.modelSummary}>
            <ModelIdentity model={model} name={modelName} />
            {(model?.thinking?.status === "supported" ||
              props.controls.thinking != null) && (
              <small>{thinkingLabel(model, props.controls.thinking, t)}</small>
            )}
          </span>
        }
      >
        <AgentModelChoices {...props} />
      </ComposerSettings>
    </div>
  );
}

function AgentModelChoices({
  catalog,
  agentId,
  defaultAgentId,
  defaultModelId,
  modelId,
  controls,
  onControlsChange,
  disabled,
  onAgentChange,
  onModelChange,
}: RunChoiceProps) {
  const { t } = useTranslation();
  const settings = useComposerSettings()!;
  const agent = catalog?.agents.find(
    (item) => item.agent_id === (agentId || defaultAgentId),
  );
  const inheritedModelId = defaultModelId ?? agent?.model_id;
  const agentGroups = [
    {
      label: t("Agents"),
      options: [
        ...(defaultAgentId
          ? [
              {
                value: "",
                label:
                  catalog?.agents.find(
                    (item) => item.agent_id === defaultAgentId,
                  )?.name ?? t("Default agent"),
                badge: t("Default"),
                description: t("Follow the project or app default."),
              },
            ]
          : []),
        ...(catalog?.agents ?? []).map((item) => ({
          value: item.agent_id,
          label: item.name,
          description: item.agent_id,
        })),
        ...(agentId && !agent
          ? [
              {
                value: agentId,
                label: t("{{name}} (unavailable)", { name: agentId }),
                disabled: true,
              },
            ]
          : []),
      ],
    },
  ];
  const model = catalog?.models?.find(
    (item) => item.model_id === (modelId ?? inheritedModelId),
  );
  if (settings.page === "model")
    return (
      <ModelOptions
        models={catalog?.models ?? []}
        defaultModelId={inheritedModelId ?? undefined}
        defaultSource={defaultModelId != null ? "thread" : "agent"}
        value={modelId}
        disabled={disabled || !catalog}
        onChange={(next) => {
          onModelChange(next);
          settings.navigate("root");
        }}
      />
    );
  if (settings.page === "agent")
    return (
      <SettingsChoices
        searchLabel={t("Search agents")}
        searchPlaceholder={t("Search agents…")}
        emptyLabel={t("No matching agents.")}
        options={agentGroups[0].options}
        value={agentId}
        disabled={disabled || !catalog}
        onChange={(next) => {
          onAgentChange(next);
          settings.navigate("root");
        }}
      />
    );
  return (
    <>
      {settings.page === "root" && (
        <>
          <SettingsRow
            label={t("Agent")}
            value={agent?.name ?? (agentId || t("Default agent"))}
            disabled={disabled || !catalog}
            onClick={() => settings.navigate("agent")}
          />
          <SettingsRow
            label={t("Model")}
            value={
              <ModelIdentity
                model={model}
                name={
                  model?.name ??
                  t("{{name}} (unavailable)", {
                    name: modelId ?? inheritedModelId ?? t("Model"),
                  })
                }
              />
            }
            disabled={disabled || !catalog}
            onClick={() => settings.navigate("model")}
          />
        </>
      )}
      <ModelControlPanel
        model={model}
        controls={controls}
        onControlsChange={onControlsChange}
        disabled={disabled || !catalog}
      />
    </>
  );
}
