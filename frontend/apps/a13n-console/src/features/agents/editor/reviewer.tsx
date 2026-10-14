import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  SearchPicker,
  Sheet,
  SheetPopup,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetPanel,
  SheetFooter,
} from "a13n-ui";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../../../shared/feedback";
import {
  jsonObject,
  TextAreaField,
  validateSettings,
} from "../../../shared/forms";
import { Section } from "../../../shared/page";
import type { Schema } from "../../../shared/api";
import type { useAgentChoices } from "../choices";
import {
  validateReviewer,
  type Reviewer,
  type ReviewTarget,
} from "../reviewer";
import type { AgentDraft } from "./draft";
import styles from "./editor.module.css";

const risks = [
  { value: "low", label: "Low risk" },
  { value: "medium", label: "Medium risk" },
  { value: "high", label: "High risk" },
  { value: "extra_high", label: "Extra high risk" },
] as const;
const actions = [
  { value: "deny", label: "Deny execution" },
  { value: "approval_required", label: "Ask for approval" },
] as const;

export function ReviewerSection({
  draft,
  choices,
  readOnly,
  mark,
  onOpen,
}: {
  draft: AgentDraft;
  choices: ReturnType<typeof useAgentChoices>;
  readOnly: boolean;
  mark?: ReactNode;
  onOpen: () => void;
}) {
  const { t } = useTranslation();
  const reviewer = draft.reviewer;
  const model = choices.data?.models.find(
    (item) => item.key === reviewer?.model,
  );
  return (
    <Section
      title={t("Automatic review")}
      description={t(
        "Only tools with automatic review use this model and policy.",
      )}
      actions={
        <>
          {mark}
          <Button type="button" variant="outline" size="sm" onClick={onOpen}>
            {readOnly ? t("View review settings") : t("Configure review")}
          </Button>
        </>
      }
    >
      <div className={styles.reviewSummary}>
        <strong>
          {reviewer?.model
            ? (model?.name ?? reviewer.model)
            : t("No review model configured")}
        </strong>
        {reviewer && (
          <span>
            {t("{{risk}} and above → {{action}}", {
              risk: t(
                risks.find(
                  (risk) =>
                    risk.value === (reviewer.risk_threshold ?? "extra_high"),
                )!.label,
              ),
              action: t(
                actions.find(
                  (action) => action.value === (reviewer.on_flagged ?? "deny"),
                )!.label,
              ),
            })}
          </span>
        )}
        {!!Object.keys(reviewer?.rules ?? {}).length && (
          <span>
            {t("{{count}} tool review rules", {
              count: Object.keys(reviewer?.rules ?? {}).length,
            })}
          </span>
        )}
      </div>
    </Section>
  );
}

/** A local copy makes cancel, Escape and backdrop dismissal discard all edits. */
export function ReviewerSettings({
  initial,
  target,
  models,
  readOnly,
  settingsSchemas,
  onApply,
  onClose,
  validation,
}: {
  initial: AgentDraft["reviewer"];
  target?: ReviewTarget;
  models: Schema["Model"][];
  readOnly: boolean;
  settingsSchemas: Record<string, Record<string, Schema["JsonValue"]>>;
  onApply: (reviewer: Reviewer) => void;
  onClose: () => void;
  validation?: Error;
}) {
  const { t } = useTranslation();
  const [value, setValue] = useState<Reviewer>(() => ({
    ...initial,
    model: initial?.model ?? "",
  }));
  const initialSettings = JSON.stringify(
    initial?.model_settings ?? {},
    null,
    2,
  );
  const [settings, setSettings] = useState(initialSettings);
  const [timeout, setTimeout] = useState(
    String(initial?.timeout_seconds ?? 120),
  );
  const [advanced, setAdvanced] = useState(false);
  const [selector, setSelector] = useState("");
  const [error, setError] = useState(validation);
  const translatedRisks = risks.map((item) => ({
    ...item,
    label: t(item.label),
  }));
  const translatedActions = actions.map((item) => ({
    ...item,
    label: t(item.label),
  }));
  function patch(change: Partial<Reviewer>) {
    setValue((current) => ({ ...current, ...change }));
    setError(undefined);
  }
  function updateRule(key: string, change: Schema["ToolReviewRule"]) {
    patch({
      rules: { ...value.rules, [key]: { ...value.rules?.[key], ...change } },
    });
  }
  function removeRule(key: string) {
    const rules = { ...value.rules };
    delete rules[key];
    patch({ rules });
  }
  function apply() {
    try {
      if (!value.model)
        throw new Error(
          t(
            "Choose a review model before saving automatic review permissions.",
          ),
        );
      const next = {
        ...value,
        ...(settings !== initialSettings
          ? { model_settings: jsonObject(settings) }
          : {}),
        ...(timeout !== String(initial?.timeout_seconds ?? 120)
          ? { timeout_seconds: Number(timeout) }
          : {}),
      };
      validateReviewer(next);
      const model = models.find((item) => item.key === next.model);
      const schema =
        model?.config.model_api && settingsSchemas[model.config.model_api];
      if (schema && next.model_settings)
        validateSettings(schema, next.model_settings);
      onApply(next);
      onClose();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause : new Error(t("Invalid configuration")),
      );
      setAdvanced(true);
    }
  }
  return (
    <Sheet
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetPopup
        className="w-full sm:max-w-xl"
        closeProps={{ "aria-label": t("Close") }}
      >
        <SheetHeader>
          <SheetTitle>{t("Automatic review settings")}</SheetTitle>
          <SheetDescription>
            {t(
              "Share one review model and default policy across tools. Changes apply when you save the agent.",
            )}
          </SheetDescription>
        </SheetHeader>
        <SheetPanel>
          <ErrorNotice error={error} />
          <div className="grid gap-5">
            <SearchPicker
              disabled={readOnly}
              label={t("Review model")}
              placeholder={t("Choose a model…")}
              emptyMessage={t(
                "No models available. Configure a provider and model first.",
              )}
              value={value.model}
              onValueChange={(model) => patch({ model })}
              groups={[
                {
                  label: t("Available models"),
                  options: models.map((model) => ({
                    value: model.key,
                    label: model.name,
                  })),
                },
              ]}
            />
            {value.model &&
              !models.some((model) => model.key === value.model) && (
                <p className="text-muted-foreground text-sm">
                  {value.model} · {t("Not in this workspace")}
                </p>
              )}
            <div className={styles.grid}>
              <ChoiceField
                readOnly={readOnly}
                label={t("Risk threshold")}
                value={value.risk_threshold ?? "extra_high"}
                options={translatedRisks}
                onValueChange={(risk) =>
                  patch({ risk_threshold: risk as Reviewer["risk_threshold"] })
                }
              />
              <ChoiceField
                readOnly={readOnly}
                label={t("At or above threshold")}
                value={value.on_flagged ?? "deny"}
                options={translatedActions}
                onValueChange={(action) =>
                  patch({ on_flagged: action as Reviewer["on_flagged"] })
                }
              />
            </div>
            <p className="text-muted-foreground text-sm">
              {t(
                "Below the threshold, execution continues. At or above it, the selected action applies. Review timeouts always deny execution.",
              )}
            </p>
            <TextAreaField
              readOnly={readOnly}
              label={t("Custom review instructions")}
              value={value.instruction ?? ""}
              maxLength={32768}
              rows={4}
              onChange={(instruction) => patch({ instruction })}
            />
            {[
              ...Object.entries(value.rules ?? {}),
              ...(target &&
              !readOnly &&
              !Object.hasOwn(value.rules ?? {}, target.selector)
                ? [[target.selector, {} as Schema["ToolReviewRule"]] as const]
                : []),
            ].map(([key, rule]) => (
              <div key={key} className={styles.reviewRule}>
                <div className={styles.reviewRuleHeading}>
                  <strong>
                    {key === target?.selector ? target.label : key}
                  </strong>
                  {!readOnly && Object.hasOwn(value.rules ?? {}, key) && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => removeRule(key)}
                    >
                      {t("Remove rule")}
                    </Button>
                  )}
                </div>
                {key === target?.selector && <small>{key}</small>}
                {!Object.hasOwn(value.rules ?? {}, key) && (
                  <p className="text-muted-foreground text-sm">
                    {t(
                      "Editing this tool adds a separate rule. Unspecified fields use the agent defaults.",
                    )}
                  </p>
                )}
                <div className={styles.grid}>
                  <ChoiceField
                    readOnly={readOnly}
                    label={t("Rule risk threshold")}
                    value={rule.risk_threshold ?? "default"}
                    options={[
                      { value: "default", label: t("Use default threshold") },
                      ...translatedRisks,
                    ]}
                    onValueChange={(risk) =>
                      updateRule(key, {
                        risk_threshold:
                          risk === "default"
                            ? null
                            : (risk as Schema["ToolRiskLevel"]),
                      })
                    }
                  />
                  <ChoiceField
                    readOnly={readOnly}
                    label={t("Rule action")}
                    value={rule.on_flagged ?? "default"}
                    options={[
                      { value: "default", label: t("Use default action") },
                      ...translatedActions,
                    ]}
                    onValueChange={(action) =>
                      updateRule(key, {
                        on_flagged:
                          action === "default"
                            ? null
                            : (action as Reviewer["on_flagged"]),
                      })
                    }
                  />
                </div>
              </div>
            ))}
            <DisclosureSection
              title={t("Advanced settings")}
              open={advanced}
              onOpenChange={setAdvanced}
            >
              <div className="grid gap-5">
                <TextAreaField
                  readOnly={readOnly}
                  label={t("Shell review instructions")}
                  value={value.shell_instruction ?? ""}
                  rows={4}
                  maxLength={32768}
                  hint={t(
                    "When supplied, these instructions replace the general review instructions for shell calls.",
                  )}
                  onChange={(shell_instruction) => patch({ shell_instruction })}
                />
                <div className={styles.grid}>
                  <ChoiceField
                    readOnly={readOnly}
                    label={t("On review failure")}
                    value={value.on_error ?? "approval_required"}
                    options={[
                      ...translatedActions,
                      { value: "allow", label: t("Allow execution") },
                    ]}
                    onValueChange={(action) =>
                      patch({ on_error: action as Reviewer["on_error"] })
                    }
                  />
                  <FormField label={t("Review timeout (seconds)")}>
                    <Input
                      readOnly={readOnly}
                      type="number"
                      min={0.001}
                      max={120}
                      step="any"
                      value={timeout}
                      onChange={(event) => setTimeout(event.target.value)}
                    />
                  </FormField>
                </div>
                <TextAreaField
                  readOnly={readOnly}
                  label={t("Review model settings (JSON)")}
                  code
                  value={settings}
                  onChange={setSettings}
                  rows={4}
                />
                {!readOnly && (
                  <div className="grid gap-2">
                    <FormField
                      label={t("Tool rule selector")}
                      description={t(
                        "Use an exact tool ID, a namespace ending in /* or .*, or * for all tools.",
                      )}
                    >
                      <Input
                        value={selector}
                        onChange={(event) => setSelector(event.target.value)}
                      />
                    </FormField>
                    <Button
                      type="button"
                      variant="outline"
                      disabled={
                        !selector || Object.hasOwn(value.rules ?? {}, selector)
                      }
                      onClick={() => {
                        patch({ rules: { ...value.rules, [selector]: {} } });
                        setSelector("");
                      }}
                    >
                      {t("Add review rule")}
                    </Button>
                  </div>
                )}
              </div>
            </DisclosureSection>
          </div>
        </SheetPanel>
        <SheetFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            {readOnly ? t("Close") : t("Cancel")}
          </Button>
          {!readOnly && (
            <Button type="button" onClick={apply}>
              {t("Apply to draft")}
            </Button>
          )}
        </SheetFooter>
      </SheetPopup>
    </Sheet>
  );
}
