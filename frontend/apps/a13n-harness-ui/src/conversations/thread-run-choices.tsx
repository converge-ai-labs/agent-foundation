import { Button, ModalFrame, SearchPicker } from "a13n-ui";
import { FoldersIcon, ShieldWarning } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import type { Schema } from "../transport/client";
import { ModelOptions } from "./model-picker";
import { EnvironmentBindings } from "../configuration/environment-bindings";
import { ProjectFolders } from "../configuration/project-folders";
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
  const settings = useComposerSettings();
  const [localEditing, setLocalEditing] =
    useState<Schema<"EnvironmentSelectionPatch">>();
  const editing = settings ? settings.environmentDraft : localEditing;
  const setEditing = settings?.setEnvironmentDraft ?? setLocalEditing;
  const roots = value?.local_roots ?? configuration?.local_roots ?? [];
  const bindings =
    value?.environment_bindings ?? configuration?.environment_bindings ?? [];
  const workingDefault =
    value?.default_environment === undefined
      ? configuration?.default_environment
      : value.default_environment;
  const count = roots.length + bindings.length;
  const editedRoots = editing?.local_roots ?? roots;
  const editedBindings = editing?.environment_bindings ?? bindings;
  const editedDefault =
    editing?.default_environment === undefined
      ? workingDefault
      : editing.default_environment;
  const available = [
    "thread-files",
    ...editedRoots.map((_, index) =>
      index ? `workspace-${index + 1}` : "workspace",
    ),
    ...editedBindings.map((item) => item.alias),
  ];
  const invalid =
    editedRoots.some((root) => !root.trim()) ||
    new Set(editedRoots).size !== editedRoots.length ||
    (!!editedDefault && !available.includes(editedDefault));
  const beginEditing = () =>
    setEditing({
      ...value,
      local_roots: roots,
      environment_bindings: bindings,
      default_environment: workingDefault ?? null,
    });
  const closeEditor = () => {
    setEditing(undefined);
    settings?.navigate("root");
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
    <div className="flex flex-col gap-6">
      <section>
        <h3 className="mb-2 font-medium">Local folders</h3>
        <ProjectFolders
          roots={editedRoots.map((path) => ({ path }))}
          allowEmpty
          onChange={(next) =>
            setEditing({
              ...editing,
              local_roots: next.map((item) => item.path),
            })
          }
        />
      </section>
      <section>
        <h3 className="mb-2 font-medium">Remote environments and default</h3>
        <EnvironmentBindings
          bindings={editedBindings}
          localRoots={editedRoots}
          defaultEnvironment={editedDefault}
          onChange={(environment_bindings, default_environment) =>
            setEditing({
              ...editing,
              environment_bindings,
              default_environment,
            })
          }
        />
      </section>
    </div>
  );
  const executionPicker = (
    <EnvironmentPicker
      catalog={catalog}
      defaultProfileId={configuration?.environment_profile_id}
      value={value?.environment_profile_id ?? undefined}
      disabled={disabled}
      onChange={(profile) => {
        const next = { ...value };
        if (profile) next.environment_profile_id = profile;
        else delete next.environment_profile_id;
        onChange(Object.keys(next).length ? next : undefined);
      }}
    />
  );
  if (settings)
    return (
      <>
        {settings.page === "root" && (
          <SettingsRow
            label="Working environments"
            value={
              count
                ? `${count} environment${count === 1 ? "" : "s"}`
                : "Thread files"
            }
            disabled={disabled || !configuration}
            onClick={() => {
              beginEditing();
              settings.navigate("environments");
            }}
          />
        )}
        {executionPicker}
        {settings.page === "environments" && (
          <fieldset disabled={disabled}>
            <p className={panelStyles.hint}>
              Local path references and remote directories for the next Run
              only. No files are copied. Steering keeps the active environment.
            </p>
            {content}
            <div className={panelStyles.footer}>{footer}</div>
          </fieldset>
        )}
      </>
    );
  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        disabled={disabled || !configuration}
        aria-label="Working environments"
        onClick={beginEditing}
      >
        <FoldersIcon aria-hidden="true" />
        {count
          ? `${count} environment${count === 1 ? "" : "s"}`
          : "Thread files"}
      </Button>
      {executionPicker}
      <ModalFrame
        open={editing !== undefined}
        onOpenChange={(open) => {
          if (!open) setEditing(undefined);
        }}
        title="Working environments"
        closeLabel="Cancel"
        description="Choose local path references and remote directories for the next Run only. No files are copied. Steering keeps the active environment."
        footer={footer}
      >
        {content}
      </ModalFrame>
    </>
  );
}

export function EnvironmentPicker({
  catalog,
  defaultProfileId,
  value,
  onChange,
  disabled,
}: {
  catalog?: Schema<"ThreadSelectorCatalog">;
  defaultProfileId?: string;
  value?: string;
  onChange: (value: string | undefined) => void;
  disabled: boolean;
}) {
  const environments = catalog?.environments ?? [];
  const inherited = environments.find(
    (item) => item.profile_id === defaultProfileId,
  );
  const selected = environments.find(
    (item) => item.profile_id === (value ?? defaultProfileId),
  );
  const description = (item: (typeof environments)[number]) =>
    item.mode === "full-control"
      ? "Runs as your host account. Not a sandbox."
      : item.description;
  const settings = useComposerSettings();
  const groups = [
    {
      label: "Execution environments",
      options: [
        {
          value: "",
          label: inherited?.name ?? defaultProfileId ?? "Default environment",
          badge: "Default",
          icon: <ShieldWarning aria-hidden="true" />,
          description: inherited
            ? `Follow the conversation default. ${description(inherited)}`
            : "The conversation's selected environment is unavailable.",
        },
        ...environments.map((item) => ({
          value: item.profile_id,
          label: item.name,
          description: description(item),
          icon: <ShieldWarning aria-hidden="true" />,
        })),
        ...(value && !selected
          ? [
              {
                value,
                label: `${value} (unavailable)`,
                disabled: true,
              },
            ]
          : []),
      ],
    },
  ];
  const placeholder =
    selected?.name ??
    (value
      ? `${value} (unavailable)`
      : (inherited?.name ?? defaultProfileId ?? "Loading environment…"));
  const unavailable = disabled || !catalog || !defaultProfileId;
  if (settings) {
    if (settings.page === "root")
      return (
        <SettingsRow
          label="Execution mode"
          value={placeholder}
          disabled={unavailable}
          onClick={() => settings.navigate("execution")}
        />
      );
    if (settings.page !== "execution") return null;
    return (
      <>
        <SettingsChoices
          label="execution modes"
          options={groups[0].options}
          value={value ?? ""}
          disabled={unavailable}
          onChange={(next) => {
            onChange(next || undefined);
            settings.navigate("root");
          }}
        />
        <p className={panelStyles.hint}>
          For your next Run only. Steering keeps the active environment.
        </p>
      </>
    );
  }
  return (
    <div
      className={styles.mode}
      data-full-control={selected?.mode === "full-control"}
      title="Applies to your next Run, not the active Run or conversation defaults."
    >
      <SearchPicker
        label="Execution mode"
        popupClassName={styles.choicePopup}
        placeholder={
          selected?.name ??
          (value
            ? `${value} (unavailable)`
            : (inherited?.name ?? defaultProfileId ?? "Loading environment…"))
        }
        emptyMessage="No environments found."
        value={value ?? ""}
        disabled={disabled || !catalog || !defaultProfileId}
        onValueChange={(next) => onChange(next || undefined)}
        groups={groups}
        footer={
          <small>
            For your next Run only. Steering keeps the active environment.
          </small>
        }
      />
    </div>
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
      <ComposerSettings kind="model">
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
