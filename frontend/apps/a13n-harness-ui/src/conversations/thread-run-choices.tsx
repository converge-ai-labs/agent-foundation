import { SearchPicker } from "a13n-ui";
import { useEffect, useRef } from "react";
import type { Schema } from "../transport/client";
import { ModelPicker } from "./model-picker";
import styles from "./new-conversation.module.css";

export function ThreadRunChoices({
  catalog,
  agentId,
  defaultAgentId,
  modelId,
  thinking,
  onThinkingChange,
  disabled,
  onAgentChange,
  onModelChange,
}: {
  catalog?: Schema<"ThreadSelectorCatalog">;
  agentId: string;
  defaultAgentId?: string;
  modelId?: string;
  thinking?: Schema<"SubmitRequest">["thinking"];
  onThinkingChange: (value: Schema<"SubmitRequest">["thinking"]) => void;
  disabled: boolean;
  onAgentChange: (value: string) => void;
  onModelChange: (value: string | undefined) => void;
}) {
  const agent = catalog?.agents.find(
    (item) => item.agent_id === (agentId || defaultAgentId),
  );
  const selectionKey = agent
    ? JSON.stringify([agent.agent_id, modelId ?? agent.model_id])
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
    }
    previousSelection.current = selectionKey;
  }, [selectionKey, onThinkingChange]);
  return (
    <div className={styles.runChoices}>
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
        defaultModelId={agent?.model_id ?? undefined}
        value={modelId}
        disabled={disabled || !catalog}
        onChange={onModelChange}
        thinking={thinking}
        onThinkingChange={onThinkingChange}
      />
    </div>
  );
}
