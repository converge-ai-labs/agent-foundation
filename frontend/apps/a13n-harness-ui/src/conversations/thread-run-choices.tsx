import { Button, ModalFrame } from "a13n-ui";
import { FoldersIcon } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import type { Schema } from "../transport/client";
import { ModelOptions } from "./model-picker";
import {
  EnvironmentsEditor,
  invalidEnvironments,
} from "../configuration/environments";
import styles from "./new-conversation.module.css";
import panelStyles from "./composer-settings.module.css";
import {
  ComposerSettings,
  SettingsChoices,
  SettingsRow,
  useComposerSettings,
} from "./composer-settings";
import { ModelControlPanel, type ModelControlProps } from "./model-controls";

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
  const [editing, setEditing] = useState<Schema<"EnvironmentSelectionPatch">>();
  const roots = value?.local_roots ?? configuration?.local_roots ?? [];
  const bindings =
    value?.environment_bindings ?? configuration?.environment_bindings ?? [];
  const workingDefault =
    value?.default_environment === undefined
      ? configuration?.default_environment
      : value.default_environment;
  const count = roots.length + bindings.length;
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
        disabled={disabled}
        onClick={() => {
          onChange(undefined);
          closeEditor();
        }}
      >
        Use conversation defaults
      </Button>
      <Button
        type="button"
        disabled={disabled || invalid || !editing}
        onClick={() => {
          if (!editing || disabled) return;
          onChange(editing);
          closeEditor();
        }}
      >
        Use for next Run
      </Button>
    </>
  );
  const content = editing && (
    <EnvironmentsEditor
      value={editing}
      profiles={catalog?.environments}
      inheritedProfile={configuration?.environment_profile_id}
      inheritLabel="Follow conversation local mode"
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
        aria-label="Environments"
        onClick={beginEditing}
      >
        <FoldersIcon aria-hidden="true" />
        {count
          ? `${count} environment${count === 1 ? "" : "s"}`
          : "Thread files"}
      </Button>
      <ModalFrame
        open={editing !== undefined}
        onOpenChange={(open) => {
          if (!open) setEditing(undefined);
        }}
        title="Environments"
        closeLabel="Cancel"
        description="Choose environments for subsequent Runs in this tab until reset. Conversation defaults and the active Run stay unchanged."
        footer={footer}
      >
        <fieldset disabled={disabled}>{content}</fieldset>
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
  const {
    catalog,
    agentId,
    defaultAgentId,
    defaultModelId,
    modelId,
    onControlsChange,
  } = props;
  const agent = catalog?.agents.find(
    (item) => item.agent_id === (agentId || defaultAgentId),
  );
  const inheritedModelId = defaultModelId ?? agent?.model_id;
  const selectedModelId = modelId ?? inheritedModelId;
  const model = catalog?.models?.find(
    (item) => item.model_id === selectedModelId,
  );
  const agentName =
    agent?.name ?? (agentId ? `${agentId} (unavailable)` : "Default agent");
  const modelName =
    model?.name ??
    (selectedModelId
      ? `${selectedModelId} (unavailable)`
      : "Model unavailable");
  const selectionKey = agent
    ? JSON.stringify([agent.agent_id, selectedModelId])
    : undefined;
  const previousSelection = useRef(selectionKey);
  useEffect(() => {
    if (selectionKey === undefined) return;
    if (
      previousSelection.current !== undefined &&
      previousSelection.current !== selectionKey
    ) {
      // Cover a collaborator's Agent change or a new inherited default as well.
      onControlsChange({});
    }
    previousSelection.current = selectionKey;
  }, [selectionKey, onControlsChange]);
  return (
    <div className={panelStyles.identity}>
      <span
        className={panelStyles.summary}
        title={`${agentName} · ${modelName}`}
        aria-label={`Agent: ${agentName}. Model: ${modelName}`}
      >
        <span>{agentName}</span>
        <span aria-hidden>·</span>
        <span>{modelName}</span>
      </span>
      <ComposerSettings>
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
  const settings = useComposerSettings()!;
  const agent = catalog?.agents.find(
    (item) => item.agent_id === (agentId || defaultAgentId),
  );
  const inheritedModelId = defaultModelId ?? agent?.model_id;
  const agentGroups = [
    {
      label: "Agents",
      options: [
        ...(defaultAgentId
          ? [
              {
                value: "",
                label:
                  catalog?.agents.find(
                    (item) => item.agent_id === defaultAgentId,
                  )?.name ?? "Default agent",
                badge: "Default",
                description: "Follow the project or app default.",
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
                label: `${agentId} (unavailable)`,
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
        label="agents"
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
            label="Agent"
            value={agent?.name ?? (agentId || "Default agent")}
            disabled={disabled || !catalog}
            onClick={() => settings.navigate("agent")}
          />
          <SettingsRow
            label="Model"
            value={
              model?.name ??
              `${modelId ?? inheritedModelId ?? "Model"} (unavailable)`
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
