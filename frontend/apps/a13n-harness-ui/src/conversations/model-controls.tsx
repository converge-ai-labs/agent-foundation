import type { Schema } from "../transport/client";
import { FastToggle } from "./fast-toggle";
import { ThinkingPicker } from "./thinking-picker";
import { ReasoningModePicker } from "./reasoning-mode-picker";
import styles from "./model-picker.module.css";

export type ModelControlValues = Pick<
  Schema<"SubmitRequest">,
  "thinking" | "fast" | "reasoning_mode"
>;

export type ModelControlProps = {
  controls: ModelControlValues;
  onControlsChange: (value: ModelControlValues) => void;
};

/** Copy before asynchronous preparation; null means inherit, false is explicit. */
export function modelControlRequest(
  values: ModelControlValues,
): ModelControlValues {
  return Object.fromEntries(
    Object.entries(values).filter(([, value]) => value != null),
  );
}

/** Shared presentation, not a generic form renderer: each control keeps its semantics. */
export function ModelControlPanel({
  model,
  controls,
  onControlsChange,
  disabled,
}: ModelControlProps & {
  model?: Schema<"ModelSummary">;
  disabled?: boolean;
}) {
  return (
    <>
      <ThinkingPicker
        model={model}
        value={controls.thinking}
        disabled={disabled}
        onChange={(thinking) => onControlsChange({ ...controls, thinking })}
      />
      <ReasoningModePicker
        control={model?.reasoning_mode}
        value={controls.reasoning_mode}
        disabled={disabled}
        onChange={(reasoning_mode) =>
          onControlsChange({ ...controls, reasoning_mode })
        }
      />
      <section className={styles.thinking} aria-label="Service speed">
        <FastToggle
          model={model}
          value={controls.fast}
          disabled={disabled}
          onChange={(fast) => onControlsChange({ ...controls, fast })}
        />
      </section>
    </>
  );
}
