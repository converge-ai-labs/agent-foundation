import type { Schema } from "../transport/client";
import { SettingsRow, useComposerSettings } from "./composer-settings";
import { ThinkingPicker, thinkingSummary } from "./thinking-picker";
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

/** Compact summaries and in-panel choices share the backend's capability catalog. */
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
  const thinking = model?.thinking;
  const reasoning = model?.reasoning_mode;
  const fast = model?.fast;
  const showReasoning =
    reasoning?.supported ||
    controls.reasoning_mode != null ||
    (reasoning && reasoning.state !== "default");
  const choose = (next: ModelControlValues) => {
    onControlsChange({ ...controls, ...next });
    settings.navigate("root");
  };
  if (settings.page === "thinking")
    return (
      <ThinkingPicker
        model={model}
        value={controls.thinking}
        disabled={disabled}
        onChange={(thinking) => choose({ thinking })}
      />
    );
  if (settings.page === "reasoning")
    return (
      <ReasoningModePicker
        control={reasoning}
        value={controls.reasoning_mode}
        disabled={disabled}
        onChange={(reasoning_mode) => choose({ reasoning_mode })}
      />
    );
  if (settings.page === "fast")
    return (
      <FastPicker
        model={model}
        value={controls.fast}
        disabled={disabled}
        onChange={(fast) => choose({ fast })}
      />
    );
  if (settings.page !== "root") return null;
  return (
    <div className={styles.modelControls}>
      <SettingsRow
        label="Thinking"
        value={
          controls.thinking == null
            ? `Default · ${thinking?.default_summary ?? "Unavailable"}`
            : thinkingSummary(model, controls.thinking)
        }
        disabled={disabled}
        onClick={() => settings.navigate("thinking")}
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
      <SettingsRow
        label="Fast mode"
        value={
          !fast?.supported
            ? "Unavailable"
            : controls.fast == null
              ? `Default · ${fast.state === "on" ? "On" : fast.state === "off" ? "Off" : "Provider default"}`
              : controls.fast
                ? "On"
                : "Off"
        }
        disabled={disabled}
        onClick={() => settings.navigate("fast")}
      />
    </div>
  );
}
