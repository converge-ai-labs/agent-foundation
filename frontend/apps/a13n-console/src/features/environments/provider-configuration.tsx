import {
  Button,
  DisclosureSection,
  SegmentedControl,
  SettingsRow,
  Switch,
} from "a13n-ui";
import { Fragment, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./environments.module.css";
import {
  applyDockerPreset,
  dockerMode,
  dockerPresets,
  type DockerMode,
} from "./docker-presets";
import {
  FormSection,
  jsonObject,
  SchemaFields,
  TextAreaField,
} from "../../shared/forms";

/**
 * The provider-specific part of a template. Ordinary fields lead;
 * the JSON escape hatch is one toggle away; mounts and rare settings stay in
 * disclosures.
 */
export function ProviderConfiguration({
  schema,
  text,
  onChange,
  readOnly = false,
  error,
  note,
  variant = "default",
}: {
  schema?: Record<string, unknown>;
  text: string;
  onChange: (value: string) => void;
  readOnly?: boolean;
  error?: string;
  /** Provider-specific guidance, read as the group's description. */
  note?: string;
  variant?: "default" | "docker";
}) {
  const { t } = useTranslation();
  const desktopId = useId();
  const [mode, setMode] = useState("fields");
  const [reset, setReset] = useState(0);
  const [custom, setCustom] = useState(false);
  const customDraft = useRef<Record<string, unknown> | undefined>(undefined);
  let value: Record<string, unknown> | undefined;
  try {
    value = jsonObject(text);
  } catch {
    // An unfinished JSON draft remains editable without losing its text.
  }
  const presets = variant === "docker" ? dockerPresets(schema) : undefined;
  const selected: DockerMode =
    presets && value && schema && !custom
      ? dockerMode(value, schema, presets)
      : "custom";
  const builtIn = !!presets && selected !== "custom";
  function chooseEnvironment(next: DockerMode) {
    if (!presets || next === selected) return;
    if (selected === "custom") customDraft.current = value;
    setCustom(next === "custom");
    setMode("fields");
    change(
      next === "custom"
        ? (customDraft.current ?? value ?? {})
        : applyDockerPreset(value ?? {}, presets[next]),
    );
  }
  const properties =
    schema?.properties && typeof schema.properties === "object"
      ? Object.entries(schema.properties)
      : [];
  const primary = schema?.["x-primary-fields"];
  const image: typeof properties = [];
  const desktop = properties.find(([key]) => key === "desktop")?.[1];
  const execution: typeof properties = [];
  const mounts: typeof properties = [];
  const common: typeof properties = [];
  const advanced: typeof properties = [];
  for (const property of properties) {
    const key = property[0];
    if (variant === "docker" && key === "image") {
      image.push([
        key,
        {
          ...(property[1] as Record<string, unknown>),
          title: "Container image",
        },
      ]);
    } else if (variant === "docker" && key === "execution")
      execution.push(property);
    else if (variant === "docker" && key === "desktop") continue;
    else if (variant === "docker" && key === "mounts") mounts.push(property);
    else if (
      variant === "docker" &&
      ["environment", "init_script", "disable_network"].includes(key)
    )
      advanced.push(property);
    else if (!Array.isArray(primary) || primary.includes(key))
      common.push(property);
    else advanced.push(property);
  }
  const change = (next: Record<string, unknown>) =>
    onChange(JSON.stringify(next, null, 2));
  const fieldsAvailable = !!schema && !!value;
  const jsonMode = !builtIn && (mode === "json" || !fieldsAvailable);
  return (
    <FormSection
      title={t("Configuration")}
      description={note}
      actions={
        <>
          {!builtIn && (
            <SegmentedControl
              label={t("Configuration editor")}
              value={jsonMode ? "json" : "fields"}
              onValueChange={setMode}
              options={[
                {
                  value: "fields",
                  label: t("Fields"),
                  disabled: !fieldsAvailable,
                },
                { value: "json", label: "JSON" },
              ]}
            />
          )}
          {!readOnly && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => {
                onChange("{}");
                setCustom(false);
                customDraft.current = undefined;
                setMode("fields");
                setReset((value) => value + 1);
              }}
            >
              {t("Reset to defaults")}
            </Button>
          )}
        </>
      }
    >
      {presets && (
        <fieldset disabled={readOnly} className={styles.environmentKinds}>
          <legend>{t("Environment type")}</legend>
          <div className={styles.environmentKindChoices}>
            {(["standard", "desktop", "custom"] as const).map((kind) => (
              <button
                type="button"
                key={kind}
                aria-pressed={selected === kind}
                className={styles.runtimeChoice}
                onClick={() => chooseEnvironment(kind)}
              >
                <span>
                  {t(
                    kind === "standard"
                      ? "Standard environment"
                      : kind === "desktop"
                        ? "Desktop environment"
                        : "Custom image",
                  )}
                </span>
              </button>
            ))}
          </div>
          <p className={styles.environmentKindDescription}>
            {t(
              selected === "standard"
                ? "Run commands and work with files. The standard image and execution method are configured automatically."
                : selected === "desktop"
                  ? "Use a Linux desktop for screenshots, clicks and typing. The desktop image and execution method are configured automatically. Your connection must support Envd HTTP; Agent tools and mount permissions are separate."
                  : "Choose your own image and advanced settings. Switching to a built-in environment restores its default advanced settings; CPU, memory and mounts are kept.",
            )}
          </p>
        </fieldset>
      )}
      {jsonMode || !value ? (
        <TextAreaField
          readOnly={readOnly}
          label={t("Template configuration (JSON)")}
          value={text}
          onChange={onChange}
          error={error}
          code
          rows={8}
        />
      ) : (
        <Fragment key={reset}>
          <fieldset disabled={readOnly} className="grid min-w-0 gap-5">
            {!builtIn && image.length > 0 && (
              <SchemaFields
                schema={{ ...schema, properties: Object.fromEntries(image) }}
                value={value}
                onChange={change}
              />
            )}
            {!builtIn && execution.length > 0 && (
              <SchemaFields
                schema={{
                  ...schema,
                  properties: Object.fromEntries(execution),
                }}
                value={value}
                onChange={(next) =>
                  change({
                    ...next,
                    ...(next.execution === "native" ? { desktop: false } : {}),
                  })
                }
              />
            )}
            {!builtIn && variant === "docker" && desktop && (
              <div className={styles.desktopSettings}>
                <SettingsRow
                  label={t("Desktop capability")}
                  description={t(
                    "Requires a desktop image and the Docker connection’s Envd HTTP route. Enabling this selects EIP execution and checks desktop readiness. Agent tools and mount permissions are configured separately.",
                  )}
                  controlId={desktopId}
                  stackOnNarrow={false}
                >
                  <Switch
                    id={desktopId}
                    checked={
                      (value.desktop ??
                        (desktop as Record<string, unknown>).default) === true
                    }
                    onCheckedChange={(enabled) =>
                      change({
                        ...value,
                        desktop: enabled,
                        ...(enabled ? { execution: "envd_http" } : {}),
                      })
                    }
                    disabled={readOnly}
                  />
                </SettingsRow>
              </div>
            )}
            <SchemaFields
              schema={{ ...schema, properties: Object.fromEntries(common) }}
              value={value}
              onChange={change}
            />
          </fieldset>
          {mounts.length > 0 && (
            <DisclosureSection title={t("Mounts")}>
              <fieldset disabled={readOnly}>
                <SchemaFields
                  schema={{ ...schema, properties: Object.fromEntries(mounts) }}
                  value={value}
                  onChange={change}
                />
              </fieldset>
            </DisclosureSection>
          )}
          {!builtIn && advanced.length > 0 && (
            <DisclosureSection
              title={t("Advanced environment configuration")}
              {...(error ? { open: true } : {})}
            >
              <fieldset disabled={readOnly}>
                <SchemaFields
                  schema={{
                    ...schema,
                    properties: Object.fromEntries(advanced),
                  }}
                  value={value}
                  onChange={change}
                />
              </fieldset>
            </DisclosureSection>
          )}
          {error && (
            <p role="alert" className="text-destructive-foreground text-xs">
              {error}
            </p>
          )}
        </Fragment>
      )}
    </FormSection>
  );
}
