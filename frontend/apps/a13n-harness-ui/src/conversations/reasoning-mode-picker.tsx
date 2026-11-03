import { useTranslation } from "../i18n";
import { modelControlMessage } from "./model-control-messages";
import { SettingsChoices } from "./composer-settings";
import type { Schema } from "../transport/client";
import styles from "./composer-settings.module.css";

export function reasoningModeLabel(
  state?: Schema<"ReasoningModeControl">["state"],
) {
  switch (state) {
    case "pro":
      return "Pro";
    case "standard":
      return "Standard";
    case "custom":
      return "Custom configuration";
    case "default":
      return "Provider default";
    default:
      return "Unavailable";
  }
}

export function ReasoningModePicker({
  control,
  value,
  disabled,
  onChange,
}: {
  control?: Schema<"ReasoningModeControl"> | null;
  value?: Schema<"SubmitRequest">["reasoning_mode"];
  disabled?: boolean;
  onChange: (value: Schema<"SubmitRequest">["reasoning_mode"]) => void;
}) {
  const { t } = useTranslation();
  return (
    <>
      <SettingsChoices
        searchLabel={t("Search reasoning modes")}
        searchPlaceholder={t("Search reasoning modes…")}
        emptyLabel={t("No matching reasoning modes.")}
        value={value ?? "default"}
        disabled={disabled}
        onChange={(next) =>
          onChange(next === "default" ? null : (next as "standard" | "pro"))
        }
        options={[
          {
            value: "default",
            label: t("Default"),
          },
          {
            value: "standard",
            label: t("Standard"),
            disabled: !control?.supported,
          },
          { value: "pro", label: t("Pro"), disabled: !control?.supported },
        ]}
      />
      <p className={styles.hint}>
        {control?.reason != null
          ? modelControlMessage(control.reason, t)
          : t(
              "Separate from reasoning effort. Pro access, usage and speed depend on the provider.",
            )}
      </p>
      {value != null && !control?.supported && (
        <p className={styles.hint}>
          {t("Unavailable selection — use default.")}
        </p>
      )}
    </>
  );
}
