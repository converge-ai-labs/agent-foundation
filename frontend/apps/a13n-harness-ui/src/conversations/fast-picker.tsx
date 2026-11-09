import { useTranslation } from "../i18n";
import { modelControlMessage } from "./model-control-messages";
import { speedLabel } from "./speed-label";
import { Button } from "a13n-ui";
import { Check } from "@phosphor-icons/react";
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
  const { t } = useTranslation();
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
    <section aria-label={t("Speed")} className={styles.speedControl}>
      <Button
        variant="ghost"
        className={styles.choice}
        aria-label={t("Use model speed")}
        title={modelControlMessage(speedLabel(control?.state), t)}
        aria-pressed={value == null}
        disabled={disabled}
        onClick={() => onChange(null)}
      >
        <span>
          {t("Model setting - {{speed}}", {
            speed: modelControlMessage(speedLabel(control?.state), t),
          })}
        </span>
        {value == null && <Check aria-hidden />}
      </Button>
      <Button
        variant="ghost"
        className={styles.choice}
        aria-label={t("Standard mode")}
        aria-pressed={value === false}
        disabled={disabled || !control?.supported}
        onClick={() => onChange(false)}
      >
        <span>{t("Standard")}</span>
        {value === false && <Check aria-hidden />}
      </Button>
      <Button
        variant="ghost"
        className={styles.choice}
        aria-label={t("Fast mode")}
        title={
          control?.reason != null
            ? modelControlMessage(control.reason, t)
            : t(
                "Faster responses may cost more and depend on provider capacity.",
              )
        }
        aria-pressed={active}
        disabled={disabled || !control?.supported}
        onClick={() => onChange(true)}
      >
        <span>{t("Fast")}</span>
        {active && <Check aria-hidden />}
      </Button>
      {showUltrafast && (
        <Button
          variant="ghost"
          className={styles.choice}
          aria-label={t("Ultrafast mode")}
          aria-pressed={ultraActive}
          title={
            control?.ultrafast_reason != null
              ? modelControlMessage(control.ultrafast_reason, t)
              : t(
                  "Requires Pro $500 or an eligible Enterprise/Edu plan. Higher usage rates apply.",
                )
          }
          disabled={disabled || !control?.ultrafast_supported}
          onClick={() => onChange("ultrafast")}
        >
          <span>{t("Ultrafast")}</span>
          {ultraActive && <Check aria-hidden />}
        </Button>
      )}
      {!control?.supported && (
        <p className={styles.hint}>
          {control?.reason != null
            ? modelControlMessage(control.reason, t)
            : t("Speed controls are unavailable.")}
        </p>
      )}
      {value != null && unavailable && (
        <p className={styles.hint}>
          {t("Unavailable selection — use the model setting.")}
        </p>
      )}
    </section>
  );
}
