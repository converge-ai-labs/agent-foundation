import { ArrowCounterClockwise, Lightning } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import type { Schema } from "../transport/client";
import styles from "./fast-toggle.module.css";

export function FastToggle({
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
  const control = model?.fast;
  const state =
    value == null ? (control?.state ?? "default") : value ? "on" : "off";
  const unavailable = !control?.supported;
  const description = unavailable
    ? (control?.reason ?? "Fast controls are unavailable.")
    : `${value == null ? "Model default" : "Next run"}: ${state}. May increase cost or credit usage. Requested setting, not guaranteed speed.`;
  return (
    <div className={styles.control}>
      <Button
        variant="outline"
        size="sm"
        aria-label="Fast mode"
        aria-pressed={state === "on"}
        data-active={state === "on"}
        disabled={disabled || unavailable}
        title={description}
        onClick={() => onChange(state !== "on")}
        className={styles.toggle}
      >
        <Lightning aria-hidden />
        Fast {state === "on" ? "On" : state === "off" ? "Off" : "Default"}
      </Button>
      {value != null && (
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Use model default for Fast"
          title="Use model default for Fast"
          disabled={disabled}
          onClick={() => onChange(null)}
        >
          <ArrowCounterClockwise aria-hidden />
        </Button>
      )}
    </div>
  );
}
