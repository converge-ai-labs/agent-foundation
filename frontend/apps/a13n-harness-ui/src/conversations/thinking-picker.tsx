import { SearchPicker } from "a13n-ui";
import type { Schema } from "../transport/client";
import styles from "./new-conversation.module.css";

type Selection = Schema<"SubmitRequest">["thinking"];

/** Provider rules and native values belong to the backend catalog, not React. */
export function ThinkingPicker({
  model,
  value,
  disabled,
  onChange,
}: {
  model?: Schema<"ModelSummary">;
  value?: Selection;
  disabled?: boolean;
  onChange: (value: Selection) => void;
}) {
  const control = model?.thinking;
  const options = control?.options ?? [];
  const selected = options.find((item) => item.value === (value ?? null));
  const unavailable = value != null && !selected;
  return (
    <div className={styles.runChoice} title={control?.reason ?? undefined}>
      <span>Thinking</span>
      <SearchPicker
        label="Thinking"
        popupClassName={styles.choicePopup}
        placeholder={control ? control.default_summary : "Unavailable"}
        value={JSON.stringify(value ?? null)}
        disabled={disabled || !control}
        emptyMessage="No thinking controls available."
        onValueChange={(key) => {
          const option = options.find(
            (item) => JSON.stringify(item.value) === key,
          );
          if (option && !option.disabled_reason) onChange(option.value);
        }}
        groups={[
          {
            label: control?.reason ?? "Thinking for this model",
            options: [
              ...options.map((item) => ({
                value: JSON.stringify(item.value),
                label:
                  item.value === null
                    ? `Default · ${control?.default_summary}`
                    : item.label,
                description:
                  item.disabled_reason ??
                  (item.value === null ? control?.reason : null) ??
                  item.description,
                disabled: !!item.disabled_reason,
              })),
              ...(unavailable
                ? [
                    {
                      value: JSON.stringify(value),
                      label: "Unavailable selection — choose Default",
                      disabled: true,
                    },
                  ]
                : []),
            ],
          },
        ]}
      />
    </div>
  );
}
