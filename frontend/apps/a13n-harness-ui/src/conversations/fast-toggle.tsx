import { Lightning } from "@phosphor-icons/react";
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
  onChange: (value: boolean) => void;
}) {
  const control = model?.fast;
  const active = value ?? control?.state === "on";
  const unavailable = !control?.supported;
  const description = unavailable
    ? (control?.reason ?? "Fast controls are unavailable.")
    : `${value == null ? "Model setting" : "Next run"}: ${active ? "Fast on" : "Fast not requested"}. May increase cost or credit usage. Requested setting, not guaranteed speed.`;
  return (
    <Button
      variant="outline"
      size="sm"
      aria-label="Fast mode"
      aria-pressed={active}
      data-active={active}
      disabled={disabled || unavailable}
      title={description}
      onClick={() => onChange(!active)}
      className={styles.toggle}
    >
      <Lightning aria-hidden weight={active ? "fill" : "regular"} />
      Fast
    </Button>
  );
}
