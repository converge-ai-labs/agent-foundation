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
  value?: boolean | null;
  disabled?: boolean;
  onChange: (value: boolean | null) => void;
}) {
  const descriptionId = useId();
  const control = model?.fast;
  const active = value ?? control?.state === "on";
  const state =
    value == null
      ? control?.state === "on"
        ? "On"
        : control?.state === "off"
          ? "Off"
          : "Provider default"
      : value
        ? "On"
        : "Off";
  return (
    <section aria-label="Fast mode">
      <div className={styles.controlHeading}>
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
        <span className={styles.controlState}>
          {value == null ? `Default · ${state}` : state}
        </span>
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
      <p id={descriptionId} className={styles.hint}>
        {control?.reason ??
          (!control?.supported
            ? "Fast controls are unavailable."
            : "May increase cost or credit usage. Requested setting, not guaranteed speed.")}
      </p>
      {value != null && !control?.supported && (
        <p className={styles.hint}>Unavailable selection — use default.</p>
      )}
    </section>
  );
}
