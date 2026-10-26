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
}: {
  value: ExecutionOverride;
  onChange?: (value: ExecutionOverride) => void;
  readOnly?: boolean;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const id = useId();
  const toolModes = [
    { value: "inherit", label: t("Inherit") },
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
    { value: "inherit", label: t("Inherit") },
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
        <ReadOnlyField label={t(compact ? "Tool calls" : "Tool execution")}>
          {
            toolModes.find(
              (item) => item.value === (value.tools?.mode ?? "inherit"),
            )?.label
          }
        </ReadOnlyField>
      ) : (
        <ChoiceField
          popupClassName={compact ? formStyles.compactChoicePopup : undefined}
          id={`${id}-tools`}
          label={t(compact ? "Tool calls" : "Tool execution")}
          value={value.tools?.mode ?? "inherit"}
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
        <ReadOnlyField label={t(compact ? "New messages" : "Steering")}>
          {
            steeringModes.find(
              (item) => item.value === (value.steering ?? "inherit"),
            )?.label
          }
        </ReadOnlyField>
      ) : (
        <ChoiceField
          popupClassName={compact ? formStyles.compactChoicePopup : undefined}
          id={`${id}-steering`}
          label={t(compact ? "New messages" : "Steering")}
          value={value.steering ?? "inherit"}
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
          label={t(
            compact ? "Wait time (seconds)" : "Foreground wait (seconds)",
          )}
        >
          {value.tools?.foreground_wait_seconds ?? t("Inherit")}
        </ReadOnlyField>
      ) : (
        <FormField
          label={t(
            compact ? "Wait time (seconds)" : "Foreground wait (seconds)",
          )}
          description={t(
            compact
              ? "How long Auto waits for tools. Empty uses the default."
              : "Used by Auto mode. Leave empty to inherit.",
          )}
        >
          <Input
            type="number"
            min={0}
            max={300}
            step="any"
            placeholder={t("Inherit")}
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
