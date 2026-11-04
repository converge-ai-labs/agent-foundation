import formStyles from "./forms.module.css";
import { ChoiceField, FormField, Input, ReadOnlyField } from "a13n-ui";
import { useId } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../api";
import { EnvironmentVariables } from "./environment-variables";

export type ExecutionOverride = Schema["ExecutionOverride"];

/** Inherited leaves stay absent in Agent defaults and single-Run options. */
export function executionOverride(
  value: ExecutionOverride,
): ExecutionOverride | undefined {
  const mode = value.tools?.mode;
  const wait = value.tools?.foreground_wait_seconds;
  const steering = value.steering;
  const environment = value.environment;
  if (
    environment &&
    Object.entries(environment).some(
      ([name, value]) =>
        !/^[A-Za-z_][A-Za-z0-9_]*$/.test(name) || value.includes("\0"),
    )
  )
    throw new Error(
      "Environment variables require portable names and values without NUL.",
    );
  if (wait != null && (!Number.isFinite(wait) || wait < 0 || wait > 300))
    throw new Error("Foreground wait must be between 0 and 300 seconds.");
  const tools = {
    ...(mode ? { mode } : {}),
    ...(wait != null ? { foreground_wait_seconds: wait } : {}),
  };
  return Object.keys(tools).length ||
    steering ||
    (environment && Object.keys(environment).length)
    ? {
        ...(Object.keys(tools).length ? { tools } : {}),
        ...(steering ? { steering } : {}),
        ...(environment && Object.keys(environment).length
          ? { environment }
          : {}),
      }
    : undefined;
}

/** Thread PATCH uses explicit nulls to reset previously overridden leaves. */
export function executionPatch(
  value: ExecutionOverride,
): ExecutionOverride | null {
  const override = executionOverride(value);
  return override
    ? {
        tools: {
          mode: override.tools?.mode ?? null,
          foreground_wait_seconds:
            override.tools?.foreground_wait_seconds ?? null,
        },
        steering: override.steering ?? null,
        environment: override.environment ?? null,
      }
    : null;
}

export function ExecutionFields({
  value,
  onChange,
  readOnly = false,
  compact = false,
  scope = "run",
}: {
  value: ExecutionOverride;
  onChange?: (value: ExecutionOverride) => void;
  readOnly?: boolean;
  compact?: boolean;
  scope?: "agent" | "thread" | "run";
}) {
  const { t } = useTranslation();
  const id = useId();
  const agentDefaults = scope === "agent";
  const inheritedLabel = t(
    scope === "thread" ? "Use agent settings" : "Use thread settings",
  );
  const toolMode = value.tools?.mode ?? (agentDefaults ? "auto" : "inherit");
  const steeringMode =
    value.steering ?? (agentDefaults ? "immediate" : "inherit");
  const toolModes = [
    ...(!agentDefaults ? [{ value: "inherit", label: inheritedLabel }] : []),
    {
      value: "inline",
      label: t(compact ? "Wait for tools" : "Inline — wait for tools"),
    },
    {
      value: "auto",
      label: t(compact ? "Auto" : "Auto — continue after foreground wait"),
    },
  ];
  const steeringModes = [
    ...(!agentDefaults ? [{ value: "inherit", label: inheritedLabel }] : []),
    {
      value: "boundary",
      label: t(compact ? "Between steps" : "At the next boundary"),
    },
    { value: "immediate", label: t("Immediately") },
  ];
  return (
    <div data-slot="execution-fields" className="grid gap-4">
      <EnvironmentVariables
        value={value.environment ?? {}}
        onChange={(environment) => onChange?.({ ...value, environment })}
        readOnly={readOnly}
      />
      {readOnly ? (
        <ReadOnlyField
          data-field-layout="setting"
          label={t("Long-running tools")}
          description={t(
            "Choose whether the agent waits for tools to finish or continues while supported tools run in the background.",
          )}
        >
          {toolModes.find((item) => item.value === toolMode)?.label}
        </ReadOnlyField>
      ) : (
        <ChoiceField
          popupClassName={compact ? formStyles.compactChoicePopup : undefined}
          id={`${id}-tools`}
          label={t("Long-running tools")}
          description={t(
            "Choose whether the agent waits for tools to finish or continues while supported tools run in the background.",
          )}
          value={toolMode}
          options={toolModes}
          onValueChange={(mode) =>
            onChange?.({
              ...value,
              tools: {
                ...value.tools,
                mode: mode === "inherit" ? null : (mode as "inline" | "auto"),
              },
            })
          }
        />
      )}
      {readOnly ? (
        <ReadOnlyField
          data-field-layout="setting"
          label={t("When new messages arrive")}
          description={t(
            "Choose whether new input waits for the next model step or interrupts the current response. Unsupported calls still wait for a safe boundary.",
          )}
        >
          {steeringModes.find((item) => item.value === steeringMode)?.label}
        </ReadOnlyField>
      ) : (
        <ChoiceField
          popupClassName={compact ? formStyles.compactChoicePopup : undefined}
          id={`${id}-steering`}
          label={t("When new messages arrive")}
          description={t(
            "Choose whether new input waits for the next model step or interrupts the current response. Unsupported calls still wait for a safe boundary.",
          )}
          value={steeringMode}
          options={steeringModes}
          onValueChange={(steering) =>
            onChange?.({
              ...value,
              steering:
                steering === "inherit"
                  ? null
                  : (steering as "boundary" | "immediate"),
            })
          }
        />
      )}
      {readOnly ? (
        <ReadOnlyField
          data-field-layout="setting"
          label={t("Wait before backgrounding (seconds)")}
        >
          {value.tools?.foreground_wait_seconds ??
            (agentDefaults ? t("Default") : inheritedLabel)}
        </ReadOnlyField>
      ) : (
        <FormField
          data-field-layout="setting"
          label={t("Wait before backgrounding (seconds)")}
          description={t(
            agentDefaults
              ? "Wait before letting a tool continue in the background. Applies to Auto and process-backed shell commands; does not stop the tool. Empty uses the default (normally 5 seconds)."
              : "Wait before letting a tool continue in the background. Applies to Auto and process-backed shell commands; does not stop the tool. Empty uses the parent settings.",
          )}
        >
          <Input
            type="number"
            min={0}
            max={300}
            step="any"
            placeholder={agentDefaults ? t("Default") : inheritedLabel}
            value={value.tools?.foreground_wait_seconds ?? ""}
            onChange={(event) =>
              onChange?.({
                ...value,
                tools: {
                  ...value.tools,
                  foreground_wait_seconds:
                    event.target.value === ""
                      ? null
                      : Number(event.target.value),
                },
              })
            }
          />
        </FormField>
      )}
    </div>
  );
}
