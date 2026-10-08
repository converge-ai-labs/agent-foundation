import { ChoiceField, FormField, Input, SettingsSection } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { isRecord } from "../../service-client";
import type { Schema } from "../../shared/api";
import { TextAreaField, jsonObject } from "../../shared/forms";
import sharedStyles from "../../shared/shared.module.css";
import styles from "./models.module.css";

type Settings = Record<string, Schema["JsonValue"]>;

/** Existing flat defaults are compatibility inputs; native settings win by key. */
export function requestDefaults(
  config?: Schema["ModelConfig-Output"],
): Settings {
  const legacy = Object.fromEntries(
    (
      [
        "max_tokens",
        "temperature",
        "top_p",
        "extra_body",
        "extra_headers",
      ] as const
    ).flatMap((name) =>
      config?.[name] == null ||
      (typeof config[name] === "object" &&
        Object.keys(config[name]).length === 0)
        ? []
        : [[name, config[name]]],
    ),
  );
  return { ...legacy, ...config?.settings };
}

/** Read API vocabulary from the same schema used to validate the JSON editor. */
function enumValues(schema: unknown): string[] {
  if (!isRecord(schema)) return [];
  if (Array.isArray(schema.enum))
    return schema.enum.filter(
      (value): value is string => typeof value === "string",
    );
  return Array.isArray(schema.anyOf) ? schema.anyOf.flatMap(enumValues) : [];
}

/** The fields and JSON are two views of one draft, never competing settings. */
export function ModelSettingsFields({
  value,
  onChange,
  schema,
  modelApi,
}: {
  value: string;
  onChange: (value: string) => void;
  schema?: Settings;
  modelApi: string;
}) {
  const { t } = useTranslation();
  let settings: Settings = {};
  let invalid = false;
  try {
    settings = jsonObject(value);
  } catch {
    invalid = true;
  }
  const properties = isRecord(schema?.properties) ? schema.properties : {};
  const responses = modelApi === "openai.responses";
  function change(key: string, next: Schema["JsonValue"] | undefined) {
    const updated = { ...settings };
    if (next === undefined) delete updated[key];
    else updated[key] = next;
    onChange(JSON.stringify(updated, null, 2));
  }
  function choice(
    key: string,
    label: string,
    defaultLabel: string,
    options: { value: string; label: string }[],
  ) {
    const current = key in settings ? JSON.stringify(settings[key]) : "default";
    const choices = [{ value: "default", label: defaultLabel }, ...options];
    if (!choices.some((option) => option.value === current))
      choices.push({ value: current, label: String(settings[key]) });
    return (
      <ChoiceField
        label={t(label)}
        disabled={invalid}
        value={current}
        options={choices}
        onValueChange={(next) =>
          change(key, next === "default" ? undefined : JSON.parse(next))
        }
      />
    );
  }
  return (
    <SettingsSection title={t("Request defaults")} variant="plain">
      <div className={styles.requestDefaults}>
        <p className={styles.stepNote}>
          {t(
            "Defaults for agents using this model. Agent and run settings can override them.",
          )}
        </p>
        <div className={sharedStyles.twoColumns}>
          {"thinking" in properties &&
            choice("thinking", "Thinking effort", t("Provider default"), [
              { value: "true", label: t("On (default effort)") },
              { value: "false", label: t("Off") },
              ...enumValues(properties.thinking).map((effort) => ({
                value: JSON.stringify(effort),
                label: t(effort.charAt(0).toUpperCase() + effort.slice(1)),
              })),
            ])}
          <FormField label={t("Max output tokens")}>
            <Input
              type="number"
              min={1}
              step={1}
              disabled={invalid}
              placeholder={t("Provider default")}
              value={
                typeof settings.max_tokens === "number"
                  ? settings.max_tokens
                  : ""
              }
              onChange={(event) =>
                change(
                  "max_tokens",
                  event.target.value === ""
                    ? undefined
                    : Number(event.target.value),
                )
              }
            />
          </FormField>
          {responses &&
            "openai_reasoning_summary" in properties &&
            choice(
              "openai_reasoning_summary",
              "Reasoning summary",
              t("Provider default"),
              enumValues(properties.openai_reasoning_summary).map(
                (summary) => ({
                  value: JSON.stringify(summary),
                  label: t(summary.charAt(0).toUpperCase() + summary.slice(1)),
                }),
              ),
            )}
          {responses &&
            choice("openai_store", "Store response", t("Default (off)"), [
              { value: "false", label: t("Off") },
              { value: "true", label: t("On") },
              { value: "null", label: t("Provider default") },
            ])}
        </div>
        {invalid && (
          <p role="status" className={styles.stepNote}>
            {t("Fix the settings JSON to use the fields above.")}
          </p>
        )}
        <TextAreaField
          label={t("Settings JSON")}
          hint={t(
            "Native request defaults. Fields above edit this same JSON. extra_body overrides native inference parameters; store secrets on the provider.",
          )}
          value={value}
          onChange={onChange}
          code
          rows={4}
        />
      </div>
    </SettingsSection>
  );
}
