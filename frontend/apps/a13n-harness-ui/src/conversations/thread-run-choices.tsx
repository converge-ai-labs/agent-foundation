import { Button, ModalFrame, SearchPicker } from "a13n-ui";
import { FoldersIcon, ShieldWarning } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import type { Schema } from "../transport/client";
import { ModelPicker } from "./model-picker";
import { EnvironmentBindings } from "../configuration/environment-bindings";
import { ProjectFolders } from "../configuration/project-folders";
import styles from "./new-conversation.module.css";

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
  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        disabled={disabled || !configuration}
        aria-label="Working environments"
        onClick={() =>
          setEditing({
            ...value,
            local_roots: roots,
            environment_bindings: bindings,
            default_environment: workingDefault ?? null,
          })
        }
      >
        <FoldersIcon aria-hidden="true" />
        {count
          ? `${count} environment${count === 1 ? "" : "s"}`
          : "Thread files"}
      </Button>
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
      <ModalFrame
        open={editing !== undefined}
        onOpenChange={(open) => {
          if (!open) setEditing(undefined);
        }}
        title="Working environments"
        closeLabel="Cancel"
        description="Choose local path references and remote directories for the next Run only. No files are copied. Steering keeps the active environment."
        footer={
          <>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                onChange(undefined);
                setEditing(undefined);
              }}
            >
              Use conversation defaults
            </Button>
            <Button
              type="button"
              disabled={invalid}
              onClick={() => {
                onChange(editing);
                setEditing(undefined);
              }}
            >
              Use for next Run
            </Button>
          </>
        }
      >
        {editing && (
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
              <h3 className="mb-2 font-medium">
                Remote environments and default
              </h3>
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
        )}
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
        groups={[
          {
            label: "Execution environments",
            options: [
              {
                value: "",
                label:
                  inherited?.name ?? defaultProfileId ?? "Default environment",
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
        ]}
        footer={
          <small>
            For your next Run only. Steering keeps the active environment.
          </small>
        }
      />
    </div>
  );
}

export function ThreadRunChoices({
  expanded = false,
  catalog,
  agentId,
  defaultAgentId,
  defaultModelId,
  modelId,
  thinking,
  onThinkingChange,
  fast,
  onFastChange,
  disabled,
  onAgentChange,
  onModelChange,
}: {
  expanded?: boolean;
  catalog?: Schema<"ThreadSelectorCatalog">;
  agentId: string;
  defaultAgentId?: string;
  defaultModelId?: string | null;
  modelId?: string;
  thinking?: Schema<"SubmitRequest">["thinking"];
  onThinkingChange: (value: Schema<"SubmitRequest">["thinking"]) => void;
  fast?: Schema<"SubmitRequest">["fast"];
  onFastChange: (value: boolean | null) => void;
  disabled: boolean;
  onAgentChange: (value: string) => void;
  onModelChange: (value: string | undefined) => void;
}) {
  const agent = catalog?.agents.find(
    (item) => item.agent_id === (agentId || defaultAgentId),
  );
  const inheritedModelId = defaultModelId ?? agent?.model_id;
  const selectionKey = agent
    ? JSON.stringify([agent.agent_id, modelId ?? inheritedModelId])
    : undefined;
  const previousSelection = useRef(selectionKey);
  useEffect(() => {
    if (selectionKey === undefined) return;
    if (
      previousSelection.current !== undefined &&
      previousSelection.current !== selectionKey
    ) {
      // Also cover a collaborator's Agent change or a new inherited default.
      onThinkingChange(null);
      onFastChange(null);
    }
    previousSelection.current = selectionKey;
  }, [selectionKey, onThinkingChange, onFastChange]);
  return (
    <div className={styles.runChoices}>
      <div className={styles.secondaryChoices} data-expanded={expanded}>
        <div className={styles.runChoice} title={agent?.name ?? agentId}>
          <span className={styles.choiceLabel}>Agent</span>
          <SearchPicker
            label="Agent"
            popupClassName={styles.choicePopup}
            placeholder={agent?.name ?? (agentId || "Default agent")}
            emptyMessage="No agents found."
            value={agentId}
            disabled={disabled || !catalog}
            onValueChange={onAgentChange}
            groups={[
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
            ]}
          />
        </div>
      </div>
      <div className={styles.modelChoice}>
        <span className={styles.choiceLabel}>Model</span>
        <ModelPicker
          models={catalog?.models ?? []}
          defaultModelId={inheritedModelId ?? undefined}
          defaultSource={defaultModelId != null ? "thread" : "agent"}
          value={modelId}
          disabled={disabled || !catalog}
          onChange={onModelChange}
          thinking={thinking}
          onThinkingChange={onThinkingChange}
          fast={fast}
          onFastChange={onFastChange}
        />
      </div>
    </div>
  );
}
