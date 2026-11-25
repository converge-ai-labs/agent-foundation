import {
  Checkbox,
  ChoiceField,
  FormField,
  Input,
  SettingsRow,
  SettingsSection,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { isRecord } from "../../service-client";
import type { Schema } from "../../shared/api";
import { TextAreaField, jsonObject } from "../../shared/forms";
import { fieldSchema } from "../../shared/forms/schema-definition";
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
export function enumValues(schema: unknown): string[] {
  if (!isRecord(schema)) return [];
  if (Array.isArray(schema.enum))
    return schema.enum.filter(
      (value): value is string => typeof value === "string",
    );
  return Array.isArray(schema.anyOf) ? schema.anyOf.flatMap(enumValues) : [];
}

function ProviderListField({
  label,
  value,
  disabled,
  onChange,
}: {
  label: string;
  value: Schema["JsonValue"] | undefined;
  disabled: boolean;
  onChange: (providers: string[] | undefined) => void;
}) {
  const { t } = useTranslation();
  const [input, setInput] = useState<{ text: string; formatted: string }>();
  const formatted = Array.isArray(value) ? value.join(", ") : "";
  return (
    <FormField label={label}>
      <Input
        disabled={disabled}
        placeholder={t("Comma-separated providers")}
        value={input?.formatted === formatted ? input.text : formatted}
        onBlur={() => setInput(undefined)}
        onChange={(event) => {
          // Keep the user's separators and spacing while typing; JSON stores the parsed list.
          const text = event.target.value;
          const providers = text
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean);
          setInput({ text, formatted: providers.join(", ") });
          onChange(providers.length ? providers : undefined);
        }}
      />
    </FormField>
  );
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
  const routingSchema =
    modelApi === "openrouter.chat_completions" &&
    "openrouter_provider" in properties
      ? fieldSchema(properties.openrouter_provider, schema ?? {})
      : undefined;
  const routingProperties = isRecord(routingSchema?.properties)
    ? routingSchema.properties
    : {};
  const routing = isRecord(settings.openrouter_provider)
    ? settings.openrouter_provider
    : {};
  function changeRouting(key: string, next: Schema["JsonValue"] | undefined) {
    const updated = { ...routing };
    if (next === undefined) delete updated[key];
    else updated[key] = next;
    change(
      "openrouter_provider",
      Object.keys(updated).length ? updated : undefined,
    );
  }
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
    scope = { values: settings, update: change },
  ) {
    const current =
      key in scope.values ? JSON.stringify(scope.values[key]) : "default";
    const choices = [{ value: "default", label: defaultLabel }, ...options];
    if (!choices.some((option) => option.value === current))
      choices.push({ value: current, label: String(scope.values[key]) });
    return (
      <ChoiceField
        label={label}
        disabled={invalid}
        value={current}
        options={choices}
        onValueChange={(next) =>
          scope.update(key, next === "default" ? undefined : JSON.parse(next))
        }
      />
    );
  }
  const booleanOptions = [
    { value: "true", label: t("On") },
    { value: "false", label: t("Off") },
  ];
  function routingChoice(
    key: string,
    label: string,
    options: { value: string; label: string }[],
  ) {
    return choice(key, label, t("Provider default"), options, {
      values: routing,
      update: changeRouting,
    });
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
            choice("thinking", t("Thinking effort"), t("Provider default"), [
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
              t("Reasoning summary"),
              t("Provider default"),
              enumValues(properties.openai_reasoning_summary).map(
                (summary) => ({
                  value: JSON.stringify(summary),
                  label: t(summary.charAt(0).toUpperCase() + summary.slice(1)),
                }),
              ),
            )}
          {responses &&
            choice("openai_store", t("Store response"), t("Default (off)"), [
              { value: "false", label: t("Off") },
              { value: "true", label: t("On") },
              { value: "null", label: t("Provider default") },
            ])}
        </div>
        {responses && (
          <SettingsRow label={t("Use WebSocket")}>
            <Checkbox
              aria-label={t("Use WebSocket")}
              disabled={invalid}
              checked={settings.openai_transport === "websocket"}
              onCheckedChange={(checked) =>
                change("openai_transport", checked ? "websocket" : "http")
              }
            />
          </SettingsRow>
        )}
        {routingSchema && (
          <SettingsSection
            title={t("OpenRouter provider routing")}
            variant="plain"
          >
            <div className={sharedStyles.twoColumns}>
              {routingChoice(
                "sort",
                t("Sort"),
                enumValues(routingProperties.sort).map((value) => ({
                  value: JSON.stringify(value),
                  label: value,
                })),
              )}
              {routingChoice(
                "data_collection",
                t("Data collection"),
                enumValues(routingProperties.data_collection).map((value) => ({
                  value: JSON.stringify(value),
                  label:
                    value === "allow"
                      ? t("Allow")
                      : value === "deny"
                        ? t("Deny")
                        : value,
                })),
              )}
              {routingChoice(
                "zdr",
                t("Zero Data Retention (ZDR)"),
                booleanOptions,
              )}
              {routingChoice(
                "allow_fallbacks",
                t("Allow fallbacks"),
                booleanOptions,
              )}
              <ProviderListField
                label={t("Only providers")}
                disabled={invalid}
                value={routing.only}
                onChange={(next) => changeRouting("only", next)}
              />
              <ProviderListField
                label={t("Ignore providers")}
                disabled={invalid}
                value={routing.ignore}
                onChange={(next) => changeRouting("ignore", next)}
              />
            </div>
          </SettingsSection>
        )}
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
