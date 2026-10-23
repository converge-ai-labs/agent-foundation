import { ChoiceField, FormField, Input, ReadOnlyField } from "a13n-ui";
import { useId } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../api";

export type ExecutionOverride = Schema["ExecutionOverride"];

/** Inherited leaves stay absent in Agent defaults and single-Run options. */
export function executionOverride(
  value: ExecutionOverride,
): ExecutionOverride | undefined {
  const mode = value.tools?.mode;
  const wait = value.tools?.foreground_wait_seconds;
  const steering = value.steering;
  if (wait != null && (!Number.isFinite(wait) || wait < 0 || wait > 300))
    throw new Error("Foreground wait must be between 0 and 300 seconds.");
  const tools = {
    ...(mode ? { mode } : {}),
    ...(wait != null ? { foreground_wait_seconds: wait } : {}),
  };
  return Object.keys(tools).length || steering
    ? {
        ...(Object.keys(tools).length ? { tools } : {}),
        ...(steering ? { steering } : {}),
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
      }
    : null;
}

export function ExecutionFields({
  value,
  onChange,
  readOnly = false,
}: {
  value: ExecutionOverride;
  onChange?: (value: ExecutionOverride) => void;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const id = useId();
  const toolModes = [
    { value: "inherit", label: t("Inherit") },
    { value: "inline", label: t("Inline — wait for tools") },
    { value: "auto", label: t("Auto — continue after foreground wait") },
  ];
  const steeringModes = [
    { value: "inherit", label: t("Inherit") },
    { value: "boundary", label: t("At the next boundary") },
    { value: "immediate", label: t("Immediately") },
  ];
  return (
    <div className="grid gap-4">
      {readOnly ? (
        <ReadOnlyField label={t("Tool execution")}>
          {
            toolModes.find(
              (item) => item.value === (value.tools?.mode ?? "inherit"),
            )?.label
          }
        </ReadOnlyField>
      ) : (
        <ChoiceField
          id={`${id}-tools`}
          label={t("Tool execution")}
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
        <ReadOnlyField label={t("Steering")}>
          {
            steeringModes.find(
              (item) => item.value === (value.steering ?? "inherit"),
            )?.label
          }
        </ReadOnlyField>
      ) : (
        <ChoiceField
          id={`${id}-steering`}
          label={t("Steering")}
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
        <ReadOnlyField label={t("Foreground wait (seconds)")}>
          {value.tools?.foreground_wait_seconds ?? t("Inherit")}
        </ReadOnlyField>
      ) : (
        <FormField
          label={t("Foreground wait (seconds)")}
          description={t("Used by Auto mode. Leave empty to inherit.")}
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
