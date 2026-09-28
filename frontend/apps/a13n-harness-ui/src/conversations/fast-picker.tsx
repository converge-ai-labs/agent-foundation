import type { Schema } from "../transport/client";
import { SettingsChoices } from "./composer-settings";
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
  const control = model?.fast;
  return (
    <>
      <SettingsChoices
        label="Fast modes"
        value={value == null ? "default" : String(value)}
        disabled={disabled}
        onChange={(next) =>
          onChange(next === "default" ? null : next === "true")
        }
        options={[
          {
            value: "default",
            label: "Default",
            description:
              control?.state === "on"
                ? "On"
                : control?.state === "off"
                  ? "Off"
                  : "Provider default",
          },
          { value: "true", label: "On", disabled: !control?.supported },
          { value: "false", label: "Off", disabled: !control?.supported },
        ]}
      />
      <p className={styles.hint}>
        {control?.reason ??
          "May increase cost or credit usage. Requested setting, not guaranteed speed."}
      </p>
      {value != null && !control?.supported && (
        <p className={styles.hint}>Unavailable selection — use default.</p>
      )}
    </>
  );
}
