import { SettingsChoices } from "./composer-settings";
import type { Schema } from "../transport/client";
import styles from "./composer-settings.module.css";

export function reasoningModeLabel(
  state?: Schema<"ReasoningModeControl">["state"],
) {
  switch (state) {
    case "pro":
      return "Pro";
    case "standard":
      return "Standard";
    case "custom":
      return "Custom configuration";
    case "default":
      return "Provider default";
    default:
      return "Unavailable";
  }
}

export function ReasoningModePicker({
  control,
  value,
  disabled,
  onChange,
}: {
  control?: Schema<"ReasoningModeControl"> | null;
  value?: Schema<"SubmitRequest">["reasoning_mode"];
  disabled?: boolean;
  onChange: (value: Schema<"SubmitRequest">["reasoning_mode"]) => void;
}) {
  return (
    <>
      <SettingsChoices
        label="reasoning modes"
        value={value ?? "default"}
        disabled={disabled}
        onChange={(next) =>
          onChange(next === "default" ? null : (next as "standard" | "pro"))
        }
        options={[
          {
            value: "default",
            label: "Default",
            description: reasoningModeLabel(control?.state),
          },
          {
            value: "standard",
            label: "Standard",
            disabled: !control?.supported,
          },
          { value: "pro", label: "Pro", disabled: !control?.supported },
        ]}
      />
      <p className={styles.hint}>
        {control?.reason ??
          "Independent of thinking effort. Pro access, usage and latency depend on the provider."}
      </p>
      {value != null && !control?.supported && (
        <p className={styles.hint}>Unavailable selection — use default.</p>
      )}
    </>
  );
}
