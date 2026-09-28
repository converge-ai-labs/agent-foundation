import type { Schema } from "../transport/client";
import { SettingsRow, useComposerSettings } from "./composer-settings";
import { ThinkingPicker } from "./thinking-picker";
import {
  ReasoningModePicker,
  reasoningModeLabel,
} from "./reasoning-mode-picker";
import { FastPicker } from "./fast-picker";
import styles from "./composer-settings.module.css";

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

/** Direct controls share the backend's capability catalog. */
export function ModelControlPanel({
  model,
  controls,
  onControlsChange,
  disabled,
}: ModelControlProps & {
  model?: Schema<"ModelSummary">;
  disabled?: boolean;
}) {
  const settings = useComposerSettings()!;
  const reasoning = model?.reasoning_mode;
  const showReasoning =
    reasoning?.supported ||
    controls.reasoning_mode != null ||
    (reasoning && reasoning.state !== "default");
  const choose = (next: ModelControlValues) => {
    onControlsChange({ ...controls, ...next });
    settings.navigate("root");
  };
  if (settings.page === "reasoning")
    return (
      <ReasoningModePicker
        control={reasoning}
        value={controls.reasoning_mode}
        disabled={disabled}
        onChange={(reasoning_mode) => choose({ reasoning_mode })}
      />
    );
  if (settings.page !== "root") return null;
  return (
    <div className={styles.modelControls}>
      <ThinkingPicker
        model={model}
        value={controls.thinking}
        disabled={disabled}
        onChange={(thinking) => onControlsChange({ ...controls, thinking })}
      />
      {showReasoning && (
        <SettingsRow
          label="Reasoning mode"
          value={
            controls.reasoning_mode == null
              ? `Default · ${reasoningModeLabel(reasoning?.state)}`
              : reasoning?.supported
                ? reasoningModeLabel(controls.reasoning_mode)
                : "Unavailable selection"
          }
          disabled={disabled}
          onClick={() => settings.navigate("reasoning")}
        />
      )}
      <FastPicker
        model={model}
        value={controls.fast}
        disabled={disabled}
        onChange={(fast) => onControlsChange({ ...controls, fast })}
      />
    </div>
  );
}
