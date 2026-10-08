import { useId } from "react";
import { Lightning } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import type { Schema } from "../transport/client";
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
  const selected =
    value == null
      ? control?.state
      : value === "ultrafast"
        ? "ultrafast"
        : value
          ? "on"
          : "off";
  const active = selected === "on";
  const ultraActive = selected === "ultrafast";
  const state = ultraActive
    ? "Ultrafast"
    : active
      ? "On"
      : selected === "off"
        ? "Off"
        : "Provider default";
  const unavailable =
    value === "ultrafast" ? !control?.ultrafast_supported : !control?.supported;
  const showUltrafast =
    control?.ultrafast_supported ||
    model?.route.startsWith("openai-codex:") ||
    ultraActive;
  return (
    <section aria-label="Fast mode" className={styles.speedControl}>
      <div className={styles.controlHeading}>
        <span>Speed</span>
        <Button
          variant="ghost"
          size="sm"
          aria-label="Use default Fast mode"
          aria-pressed={value == null}
          disabled={disabled}
          onClick={() => onChange(null)}
        >
          {value == null ? "Using default" : "Use default"}
        </Button>
      </div>
      <div className={styles.speedToggles}>
        <Button
          variant="outline"
          size="sm"
          className={styles.fastToggle}
          aria-label="Fast mode"
          aria-describedby={descriptionId}
          aria-pressed={active}
          disabled={disabled || !control?.supported}
          onClick={() => onChange(!active)}
        >
          <Lightning aria-hidden weight={active ? "fill" : "regular"} />
          Fast
        </Button>
        {showUltrafast && (
          <Button
            variant="outline"
            size="sm"
            className={styles.fastToggle}
            aria-label="Ultrafast mode"
            aria-describedby={descriptionId}
            aria-pressed={ultraActive}
            title={control?.ultrafast_reason ?? undefined}
            disabled={disabled || !control?.ultrafast_supported}
            onClick={() => onChange(ultraActive ? false : "ultrafast")}
          >
            <Lightning aria-hidden weight={ultraActive ? "fill" : "regular"} />
            Ultrafast
          </Button>
        )}
        <span className={styles.controlState}>
          {value != null && unavailable ? "Unavailable selection" : state}
        </span>
      </div>
      <p id={descriptionId} className={styles.hint}>
        {control?.reason ??
          (!control?.supported
            ? "Fast controls are unavailable."
            : "May increase cost or credit usage. Requested setting, not guaranteed speed.")}
      </p>
      {showUltrafast && (
        <p className={styles.hint}>
          {control?.ultrafast_reason ??
            "Ultrafast requires Pro $500 or an eligible Enterprise/Edu plan. Higher usage rates apply; account access is checked by OpenAI."}
        </p>
      )}
      {value != null && unavailable && (
        <p className={styles.hint}>Unavailable selection — use default.</p>
      )}
    </section>
  );
}
