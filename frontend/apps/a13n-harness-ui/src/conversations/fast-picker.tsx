import { useId } from "react";
import { Button } from "a13n-ui";
import { Check } from "@phosphor-icons/react";
import type { Schema } from "../transport/client";
import { speedLabel } from "./speed-label";
import styles from "./composer-settings.module.css";

export function FastPicker({
  model,
  value,
  disabled,
  onChange,
}: {
  model?: Schema<"ModelSummary">;
  value?: Schema<"SubmitRequest">["fast"];
  disabled?: boolean;
  onChange: (value: Schema<"SubmitRequest">["fast"]) => void;
}) {
  const descriptionId = useId();
  const control = model?.fast;
  const active = value === true;
  const ultraActive = value === "ultrafast";
  const unavailable =
    value === "ultrafast" ? !control?.ultrafast_supported : !control?.supported;
  const showUltrafast =
    control?.ultrafast_supported ||
    model?.route.startsWith("openai-codex:") ||
    ultraActive;
  return (
    <section aria-label="Speed" className={styles.speedControl}>
      <Button
        variant="ghost"
        className={styles.choice}
        aria-label="Use model speed"
        aria-pressed={value == null}
        disabled={disabled}
        onClick={() => onChange(null)}
      >
        <span>
          Model default
          <small>Model setting: {speedLabel(control?.state)}</small>
        </span>
        {value == null && <Check aria-hidden />}
      </Button>
      <Button
        variant="ghost"
        className={styles.choice}
        aria-label="Standard mode"
        aria-pressed={value === false}
        disabled={disabled || !control?.supported}
        onClick={() => onChange(false)}
      >
        <span>Standard</span>
        {value === false && <Check aria-hidden />}
      </Button>
      <Button
        variant="ghost"
        className={styles.choice}
        aria-label="Fast mode"
        aria-describedby={descriptionId}
        aria-pressed={active}
        disabled={disabled || !control?.supported}
        onClick={() => onChange(true)}
      >
        <span>Fast</span>
        {active && <Check aria-hidden />}
      </Button>
      {showUltrafast && (
        <Button
          variant="ghost"
          className={styles.choice}
          aria-label="Ultrafast mode"
          aria-describedby={descriptionId}
          aria-pressed={ultraActive}
          title={control?.ultrafast_reason ?? undefined}
          disabled={disabled || !control?.ultrafast_supported}
          onClick={() => onChange("ultrafast")}
        >
          <span>Ultrafast</span>
          {ultraActive && <Check aria-hidden />}
        </Button>
      )}
      <p id={descriptionId} className={styles.hint}>
        {control?.reason ??
          (!control?.supported
            ? "Speed controls are unavailable."
            : "May use more credits or cost more. Speed isn't guaranteed.")}
      </p>
      {showUltrafast && (
        <p className={styles.hint}>
          {control?.ultrafast_reason ??
            "Requires Pro $500 or an eligible Enterprise/Edu plan. Higher usage rates apply. OpenAI checks account access."}
        </p>
      )}
      {value != null && unavailable && (
        <p className={styles.hint}>
          Unavailable selection — use the model setting.
        </p>
      )}
    </section>
  );
}
