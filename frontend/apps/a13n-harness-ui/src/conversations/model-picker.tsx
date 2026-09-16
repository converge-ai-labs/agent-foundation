import { SearchPicker } from "a13n-ui";
import type { Schema } from "../transport/client";
import styles from "./new-conversation.module.css";

export function ModelPicker({
  models,
  defaultModelId,
  value,
  disabled,
  onChange,
}: {
  models: Schema<"ModelSummary">[];
  defaultModelId?: string;
  value?: string;
  disabled?: boolean;
  onChange: (value: string | undefined) => void;
}) {
  const defaultModel = models.find((item) => item.model_id === defaultModelId);
  return (
    <div className={styles.runChoice}>
      <span>Model</span>
      <SearchPicker
        label="Model"
        placeholder={
          defaultModel ? `Auto · ${defaultModel.name}` : "Agent default"
        }
        popupClassName={styles.choicePopup}
        emptyMessage="No models found."
        value={value ?? ""}
        disabled={disabled}
        onValueChange={(next) => onChange(next || undefined)}
        groups={[
          {
            label: "Models",
            options: [
              {
                value: "",
                label: "Agent default",
                description: defaultModel
                  ? `Use ${defaultModel.name}, configured by the selected agent.`
                  : "Follow the selected agent's model.",
              },
              ...models.map((item) => ({
                value: item.model_id,
                label: item.name,
                description: `${item.model_id} · ${item.route}`,
              })),
              ...(value && !models.some((item) => item.model_id === value)
                ? [{ value, label: `${value} (unavailable)`, disabled: true }]
                : []),
            ],
          },
        ]}
      />
    </div>
  );
}
