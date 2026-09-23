import { Button, ToggleGroup, ToggleGroupItem } from "a13n-ui";
import { useId } from "react";
import type { Schema } from "../transport/client";
import styles from "./model-picker.module.css";

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
  const descriptionId = useId();
  // Unsupported routes need no empty section, but a stale override must stay resettable.
  if (
    !control?.supported &&
    value == null &&
    (!control || control.state === "default")
  )
    return null;
  return (
    <section className={styles.thinking} aria-label="Reasoning mode">
      <div className={styles.heading}>
        <span>Reasoning mode</span>
        <Button
          variant="ghost"
          size="sm"
          aria-pressed={value == null}
          disabled={disabled}
          onClick={() => onChange(null)}
        >
          {value == null ? "Using default" : "Use default"}
        </Button>
      </div>
      <ToggleGroup
        aria-label="Reasoning mode"
        aria-describedby={descriptionId}
        className={styles.levels}
        value={value == null ? [] : [value]}
        disabled={disabled || !control?.supported}
        onValueChange={(keys) => {
          const next = keys[0];
          if (next === "standard" || next === "pro") onChange(next);
        }}
      >
        <ToggleGroupItem value="standard">Standard</ToggleGroupItem>
        <ToggleGroupItem value="pro">Pro</ToggleGroupItem>
      </ToggleGroup>
      <p id={descriptionId} className={styles.hint}>
        Model default: {reasoningModeLabel(control?.state)}
        {value != null &&
          ` · ${control?.supported ? "Override for next run" : "Unavailable selection — use default"}`}
      </p>
      <p className={styles.hint}>
        {control?.reason ??
          "Independent of thinking effort. Pro access, usage and latency depend on the provider."}
      </p>
    </section>
  );
}
