import { Button, FormField, Input } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { SwitchField } from "../../../shared/forms";
import { FieldHelp } from "../../../shared/forms/field-help";
import {
  subagentLimitsError,
  type Subagent,
  type SubagentMode,
  type SubagentLimits,
} from "../subagents";

const extraLimits = [
  ["input_tokens_limit", "Input token limit"],
  ["output_tokens_limit", "Output token limit"],
  ["total_tokens_limit", "Total token limit"],
  ["per_request_input_tokens_limit", "Input tokens per request"],
  ["tool_calls_limit", "Tool call limit"],
  ["cost_limit", "Cost limit (USD)"],
] as const;

export function SubagentUsageLimits({
  mode,
  value,
  onChange,
  readOnly,
}: {
  mode: SubagentMode;
  value: Subagent["usage_limits"];
  onChange: (value: Subagent["usage_limits"]) => void;
  readOnly: boolean;
}) {
  const { t } = useTranslation();
  const unsupported = mode === "async" && subagentLimitsError(mode, value);
  function field(key: keyof SubagentLimits, label: string) {
    const count =
      key === "request_limit"
        ? value?.request_limit === undefined
          ? 50
          : value.request_limit
        : value?.[key];
    return (
      <FormField
        key={key}
        label={t(label)}
        readOnly={readOnly}
        data-field-layout="setting"
        labelAction={
          key === "request_limit" ? (
            <FieldHelp label={t("About usage limits")}>
              {t(
                "Limits apply to each child invocation. Empty removes this additional limit; other runtime limits still apply. Async supports only model request counts.",
              )}
            </FieldHelp>
          ) : undefined
        }
      >
        <Input
          type="number"
          min={0}
          step={key === "cost_limit" ? "any" : 1}
          placeholder={t("No limit")}
          value={count == null ? "" : String(count)}
          onChange={(event) =>
            onChange({
              ...value,
              [key]:
                event.target.value === "" ? null : Number(event.target.value),
            })
          }
        />
      </FormField>
    );
  }
  return (
    <div className="grid gap-4">
      <SwitchField
        label={t("Override usage limits")}
        checked={value != null}
        readOnly={readOnly}
        onCheckedChange={(enabled) =>
          onChange(enabled ? { request_limit: 50 } : null)
        }
      />
      {value && (
        <>
          {field("request_limit", "Model request limit")}
          {mode === "inline" && (
            <>
              {extraLimits.map(([key, label]) => field(key, label))}
              <SwitchField
                label={t("Count tokens before requests")}
                readOnly={readOnly}
                checked={value.count_tokens_before_request ?? false}
                onCheckedChange={(count_tokens_before_request) =>
                  onChange({ ...value, count_tokens_before_request })
                }
              />
            </>
          )}
          {unsupported && (
            <div className="grid gap-2">
              <p role="alert" className="text-destructive-foreground text-sm">
                {t(unsupported)}
              </p>
              {!readOnly && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    onChange({ request_limit: value.request_limit })
                  }
                >
                  {t("Keep only the model request limit")}
                </Button>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
