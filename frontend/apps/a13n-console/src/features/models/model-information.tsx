import { ChoiceField, FormField, Input, Switch } from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import styles from "../../shared/shared.module.css";
import modelStyles from "./models.module.css";
import { ModelPricing } from "./model-pricing";

export function ModelInformation({
  value,
  onChange,
}: {
  value: Schema["ModelDeclarations-Input"];
  onChange: (value: Schema["ModelDeclarations-Input"]) => void;
}) {
  const { t } = useTranslation();
  const options = [
    { value: "unknown", label: t("Unknown") },
    { value: "true", label: t("Supported") },
    { value: "false", label: t("Unsupported") },
  ];
  return (
    <section className={styles.stack}>
      <div className={styles.twoColumns}>
        <fieldset className={modelStyles.capabilities}>
          <legend>{t("Input capabilities")}</legend>
          {(
            [
              "image_understanding",
              "audio_understanding",
              "video_understanding",
            ] as const
          ).map((capability, index) => {
            const label = ["Images", "Audio", "Video"][index];
            return (
              <label key={capability} className={modelStyles.modelStatus}>
                <Switch
                  aria-label={t(label)}
                  checked={value.capabilities?.includes(capability) ?? false}
                  onCheckedChange={(enabled) =>
                    onChange({
                      ...value,
                      capabilities: enabled
                        ? [...(value.capabilities ?? []), capability]
                        : value.capabilities?.filter(
                            (item) => item !== capability,
                          ),
                    })
                  }
                />
                {t(label)}
              </label>
            );
          })}
        </fieldset>
        <FormField label={t("Context window")}>
          <Input
            type="number"
            min={1}
            step={1}
            placeholder={t("Unknown")}
            value={value.context_window_tokens ?? ""}
            onChange={(event) =>
              onChange({
                ...value,
                context_window_tokens:
                  event.target.value === "" ? null : Number(event.target.value),
              })
            }
          />
        </FormField>
        <ChoiceField
          label={t("Tool calling support")}
          options={options}
          value={String(value.supports_tools ?? "unknown")}
          onValueChange={(next) =>
            onChange({
              ...value,
              supports_tools: next === "unknown" ? null : next === "true",
            })
          }
        />
        <ChoiceField
          label={t("Structured output")}
          options={options}
          value={String(value.structured_output ?? "unknown")}
          onValueChange={(next) =>
            onChange({
              ...value,
              structured_output: next === "unknown" ? null : next === "true",
            })
          }
        />
      </div>
      <ModelPricing
        value={value.pricing ?? null}
        onChange={(pricing) => onChange({ ...value, pricing })}
      />
    </section>
  );
}
