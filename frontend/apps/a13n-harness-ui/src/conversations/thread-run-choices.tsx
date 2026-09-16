import { SearchPicker } from "a13n-ui";
import type { Schema } from "../transport/client";
import { ModelPicker } from "./model-picker";
import styles from "./new-conversation.module.css";

export function ThreadRunChoices({
  catalog,
  agentId,
  modelId,
  disabled,
  onAgentChange,
  onModelChange,
}: {
  catalog?: Schema<"ThreadSelectorCatalog">;
  agentId: string;
  modelId?: string;
  disabled: boolean;
  onAgentChange: (value: string) => void;
  onModelChange: (value: string | undefined) => void;
}) {
  const agent = catalog?.agents.find((item) => item.agent_id === agentId);
  return (
    <div className={styles.runChoices}>
      <div className={styles.runChoice}>
        <span>Agent</span>
        <SearchPicker
          label="Agent"
          popupClassName={styles.choicePopup}
          placeholder={agentId}
          emptyMessage="No agents found."
          value={agentId}
          disabled={disabled || !catalog}
          onValueChange={onAgentChange}
          groups={[
            {
              label: "Agents",
              options: (catalog?.agents ?? []).map((item) => ({
                value: item.agent_id,
                label: item.name,
                description: item.agent_id,
              })),
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
      />
    </div>
  );
}
