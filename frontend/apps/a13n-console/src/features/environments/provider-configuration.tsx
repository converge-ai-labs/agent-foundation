import {
  Button,
  DisclosureSection,
  SegmentedControl,
  SettingsRow,
  Switch,
} from "a13n-ui";
import { Fragment, useId, useRef, useState, type ReactNode } from "react";
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
  children,
}: {
  schema?: Record<string, unknown>;
  text: string;
  onChange: (value: string) => void;
  readOnly?: boolean;
  error?: string;
  /** Provider-specific guidance, read as the group's description. */
  note?: string;
  variant?: "default" | "docker" | "e2b" | "daytona" | "modal";
  /** Template settings that close the group, such as its lifecycle. */
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  const desktopId = useId();
  const [mode, setMode] = useState("fields");
  const [reset, setReset] = useState(0);
  const [custom, setCustom] = useState(false);
  const [e2bCustom, setE2bCustom] = useState(false);
  const e2bCustomDraft = useRef<string | undefined>(undefined);
  const customDraft = useRef<Record<string, unknown> | undefined>(undefined);
  let value: Record<string, unknown> | undefined;
  try {
    value = jsonObject(text);
  } catch {
    // An unfinished JSON draft remains editable without losing its text.
  }
  const e2bDefaultTemplate = value?.desktop === true ? "auto" : "base";
  const e2bRequiresEnvd =
    value?.execution === "envd_http" || value?.desktop === true;
  const e2bUsesCustomTemplate =
    (e2bRequiresEnvd && value?.desktop !== true) ||
    e2bCustom ||
    (typeof value?.template === "string" &&
      value.template !== e2bDefaultTemplate);
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
  const e2bTemplate = properties.find(([key]) => key === "template")?.[1];
  const desktop = properties.find(([key]) => key === "desktop")?.[1];
  const execution: typeof properties = [];
  const mounts: typeof properties = [];
  const common: typeof properties = [];
  const advanced: typeof properties = [];
  for (const property of properties) {
    const key = property[0];
    if (
      variant === "e2b" &&
      e2bRequiresEnvd &&
      [
        "root",
        "user",
        "python",
        "max_file_bytes",
        "max_observation_bytes",
        "max_active_observations",
        "max_retained_output_bytes",
        "max_query_entries",
      ].includes(key)
    )
      continue;
    if (variant === "e2b" && key === "template") continue;
    if (
      (variant === "e2b" || variant === "daytona" || variant === "modal") &&
      key.startsWith("desktop_") &&
      value?.desktop !== true
    )
      continue;
    if (variant === "docker" && key === "image") {
      image.push([
        key,
        {
          ...(property[1] as Record<string, unknown>),
          title: "Container image",
        },
      ]);
    } else if (
      (variant === "docker" || variant === "e2b") &&
      key === "execution"
    )
      execution.push(property);
    else if ((variant === "docker" || variant === "e2b") && key === "desktop")
      continue;
    else if (variant === "docker" && key === "mounts") mounts.push(property);
    else if (variant === "e2b" && key === "allow_internet_access")
      common.push([
        key,
        {
          ...(property[1] as Record<string, unknown>),
          description: "Allow the sandbox to connect to external services.",
        },
      ]);
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
    onChange(
      JSON.stringify(
        (variant === "daytona" || variant === "modal") && next.desktop !== true
          ? { ...next, desktop_assistance: false }
          : next,
        null,
        2,
      ),
    );
  const fieldsAvailable = !!schema && !!value;
  const jsonMode = mode === "json" || !fieldsAvailable;
  return (
    <FormSection
      title={t("Configuration")}
      description={note}
      actions={
        <>
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
          {!readOnly && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => {
                onChange("{}");
                setCustom(false);
                setE2bCustom(false);
                e2bCustomDraft.current = undefined;
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
      {presets && !jsonMode && (
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
                    ...(next.execution === "native"
                      ? { desktop: false, desktop_assistance: false }
                      : {}),
                    ...(variant === "e2b" &&
                    next.execution === "envd_http" &&
                    ["base", "desktop"].includes(
                      String(next.template ?? "base"),
                    )
                      ? { template: "" }
                      : {}),
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
            <div
              className={variant === "e2b" ? styles.e2bSettings : "contents"}
            >
              {variant === "e2b" && desktop && (
                <SettingsRow
                  label={t("Desktop environment")}
                  description={t(
                    "Screenshots, mouse and physical keyboard input through Envd. Literal text input is not supported on Linux.",
                  )}
                  controlId={desktopId}
                  stackOnNarrow={false}
                >
                  <Switch
                    id={desktopId}
                    checked={value.desktop === true}
                    disabled={readOnly}
                    onCheckedChange={(enabled) =>
                      change({
                        ...value,
                        desktop: enabled,
                        ...(enabled
                          ? { execution: "envd_http" }
                          : { desktop_assistance: false }),
                        ...(enabled &&
                        (!value.template ||
                          ["base", "desktop"].includes(String(value.template)))
                          ? { template: "auto", desktop_assistance: true }
                          : !enabled && value.template === "auto"
                            ? { template: "base", execution: "native" }
                            : {}),
                      })
                    }
                  />
                </SettingsRow>
              )}
              {variant === "e2b" && e2bTemplate && (
                <div className={styles.e2bTemplate}>
                  <div className={styles.e2bTemplateHeading}>
                    <span>{t("E2B template")}</span>
                    <SegmentedControl
                      label={t("E2B template")}
                      value={e2bUsesCustomTemplate ? "custom" : "official"}
                      options={[
                        {
                          value: "official",
                          label: t(
                            value.desktop === true
                              ? "Automatic desktop"
                              : "Official default",
                          ),
                          disabled:
                            readOnly ||
                            (e2bRequiresEnvd && value.desktop !== true),
                        },
                        {
                          value: "custom",
                          label: t("Custom template"),
                          disabled: readOnly,
                        },
                      ]}
                      onValueChange={(next) => {
                        if (
                          next ===
                          (e2bUsesCustomTemplate ? "custom" : "official")
                        )
                          return;
                        if (next === "custom") {
                          setE2bCustom(true);
                          change({
                            ...value,
                            template: e2bCustomDraft.current ?? "",
                          });
                        } else {
                          if (typeof value.template === "string")
                            e2bCustomDraft.current = value.template;
                          setE2bCustom(false);
                          change({
                            ...value,
                            template: e2bDefaultTemplate,
                            ...(value.desktop === true
                              ? { desktop_assistance: true }
                              : {}),
                          });
                        }
                      }}
                    />
                  </div>
                  {e2bUsesCustomTemplate ? (
                    <SchemaFields
                      schema={{
                        ...schema,
                        properties: {
                          template: {
                            ...(e2bTemplate as Record<string, unknown>),
                            description:
                              value.desktop === true
                                ? "Enter an operator-built template with a13n-envd and desktop components preinstalled."
                                : e2bRequiresEnvd
                                  ? "Enter an operator-built template with a13n-envd preinstalled."
                                  : "Enter an existing E2B template name or ID. Desktop processes and Computer tools will remain disabled.",
                          },
                        },
                      }}
                      value={value}
                      onChange={change}
                    />
                  ) : (
                    <div className={styles.e2bTemplateSummary}>
                      <strong>
                        {value.desktop === true
                          ? t("Automatic desktop")
                          : e2bDefaultTemplate}
                      </strong>
                      <span>
                        {t(
                          value.desktop === true
                            ? "The first desktop creation usually takes 1–2 minutes to prepare a private template; it may take longer. Later creations reuse it. You can leave this page while creation continues."
                            : "A standard sandbox for commands and files.",
                        )}
                      </span>
                    </div>
                  )}
                </div>
              )}
            </div>
            {variant === "e2b" && value.desktop === true && (
              <p className={styles.e2bHelp}>
                {t(
                  "Configure Agent Computer tools and mount permissions separately.",
                )}
              </p>
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
      {children}
    </FormSection>
  );
}
