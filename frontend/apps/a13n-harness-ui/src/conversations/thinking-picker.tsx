import { SettingsChoices } from "./composer-settings";
import type { Schema } from "../transport/client";
import styles from "./composer-settings.module.css";

type Selection = Schema<"SubmitRequest">["thinking"];

export function thinkingSummary(
  model: Schema<"ModelSummary"> | undefined,
  value: Selection,
) {
  const control = model?.thinking;
  if (value != null) {
    const selected = control?.options.find((item) => item.value === value);
    return !selected || selected.disabled_reason
      ? "Unavailable thinking"
      : selected.label;
  }
  return control?.status === "supported" ? control.default_summary : undefined;
}

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
  const unavailable =
    value != null && (!selected || !!selected.disabled_reason);
  return (
    <>
      <SettingsChoices
        label="thinking levels"
        value={JSON.stringify(value ?? null)}
        disabled={disabled}
        onChange={(key) => onChange(JSON.parse(key) as Selection)}
        options={[
          ...(!options.some((item) => item.value === null)
            ? [
                {
                  value: "null",
                  label: "Default",
                  description:
                    control?.default_summary ?? "Follow model settings",
                },
              ]
            : []),
          ...options.map((item) => ({
            value: JSON.stringify(item.value),
            label: item.value === null ? "Default" : item.label,
            description:
              item.disabled_reason ??
              (item.value === null
                ? control?.default_summary
                : item.description),
            disabled: !!item.disabled_reason,
          })),
        ]}
      />
      {unavailable && (
        <p className={styles.hint}>
          {selected?.disabled_reason ?? "Unavailable selection — use default."}
        </p>
      )}
      {(!control || control.reason) && (
        <p className={styles.hint}>
          {control?.reason ?? "No thinking controls available."}
        </p>
      )}
    </>
  );
}
