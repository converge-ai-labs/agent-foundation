import { useTranslation } from "../i18n";
import { modelControlMessage } from "./model-control-messages";
import type { Schema } from "../transport/client";
import { SettingsRow, useComposerSettings } from "./composer-settings";
import { ThinkingPicker } from "./thinking-picker";
import {
  ReasoningModePicker,
  reasoningModeLabel,
} from "./reasoning-mode-picker";
import { FastPicker } from "./fast-picker";
import { speedLabel } from "./speed-label";
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
  const { t } = useTranslation();
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
  if (settings.page === "thinking")
    return (
      <ThinkingPicker
        model={model}
        value={controls.thinking}
        disabled={disabled}
        onChange={(thinking) => choose({ thinking })}
      />
    );
  if (settings.page === "speed")
    return (
      <FastPicker
        model={model}
        value={controls.fast}
        disabled={disabled}
        onChange={(fast) => choose({ fast })}
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
  if (settings.page !== "root") return null;
  return (
    <div className={styles.modelControls}>
      <SettingsRow
        label={t("Thinking")}
        value={
          controls.thinking == null
            ? t("Default")
            : modelControlMessage(
                model?.thinking?.options.find(
                  (item) => item.value === controls.thinking,
                )?.label ?? "Unavailable selection",
                t,
              )
        }
        disabled={disabled}
        onClick={() => settings.navigate("thinking")}
      />
      {showReasoning && (
        <SettingsRow
          label={t("Reasoning mode")}
          value={
            controls.reasoning_mode == null
              ? reasoning?.state === "default"
                ? modelControlMessage(reasoningModeLabel(reasoning.state), t)
                : t("{{mode}} · Model default", {
                    mode: modelControlMessage(
                      reasoningModeLabel(reasoning?.state),
                      t,
                    ),
                  })
              : reasoning?.supported
                ? modelControlMessage(
                    reasoningModeLabel(controls.reasoning_mode),
                    t,
                  )
                : t("Unavailable selection")
          }
          disabled={disabled}
          onClick={() => settings.navigate("reasoning")}
        />
      )}
      <SettingsRow
        label={t("Speed")}
        value={
          controls.fast == null
            ? modelControlMessage(speedLabel(model?.fast?.state), t)
            : modelControlMessage(
                speedLabel(
                  controls.fast === "ultrafast"
                    ? "ultrafast"
                    : controls.fast
                      ? "on"
                      : "off",
                ),
                t,
              )
        }
        disabled={disabled}
        onClick={() => settings.navigate("speed")}
      />
    </div>
  );
}
