import { SearchPicker } from "a13n-ui";
import { ShieldWarning } from "@phosphor-icons/react";
import { useEffect, useRef } from "react";
import type { Schema } from "../transport/client";
import { ModelPicker } from "./model-picker";
import { FastToggle } from "./fast-toggle";
import styles from "./new-conversation.module.css";

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
      <FastToggle
        model={catalog?.models?.find(
          (item) => item.model_id === (modelId ?? inheritedModelId),
        )}
        value={fast}
        disabled={disabled || !catalog}
        onChange={onFastChange}
      />
      <div className={styles.secondaryChoices} data-expanded={expanded}>
        <div className={styles.runChoice} title={agent?.name ?? agentId}>
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
        <ModelPicker
          models={catalog?.models ?? []}
          defaultModelId={inheritedModelId ?? undefined}
          defaultSource={defaultModelId != null ? "thread" : "agent"}
          value={modelId}
          disabled={disabled || !catalog}
          onChange={onModelChange}
          thinking={thinking}
          onThinkingChange={onThinkingChange}
        />
      </div>
    </div>
  );
}
