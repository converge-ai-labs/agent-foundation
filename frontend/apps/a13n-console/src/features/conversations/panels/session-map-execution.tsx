import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Tooltip, TooltipTrigger, TooltipPopup } from "a13n-ui";
import {
  BrainIcon,
  WrenchIcon,
  HandPalmIcon,
  GitBranchIcon,
  ArrowsInLineVerticalIcon,
  ArrowBendUpRightIcon,
  DatabaseIcon,
  CodeIcon,
} from "@phosphor-icons/react";
import { StatePill } from "../../../shared/feedback";
import { isObject } from "../projection";
import type { Execution, ExecutionKind, ExecutionStep } from "../execution";
import styles from "./session-map.module.css";

const appearance = {
  llm: { label: "LLM call", icon: BrainIcon },
  tool: { label: "Tool call", icon: WrenchIcon },
  hitl: { label: "HITL", icon: HandPalmIcon },
  subagent: { label: "Subagent", icon: GitBranchIcon },
  compaction: { label: "Compaction", icon: ArrowsInLineVerticalIcon },
  handoff: { label: "Handoff", icon: ArrowBendUpRightIcon },
  memory: { label: "Memory recall", icon: DatabaseIcon },
  codeact: { label: "CodeAct", icon: CodeIcon },
} satisfies Record<ExecutionKind, { label: string; icon: typeof BrainIcon }>;

export function MapExecution({ execution }: { execution: Execution }) {
  const { t } = useTranslation();
  const children = useMemo(() => {
    const scopes = new Map(
      execution.steps
        .filter((step) => step.childScope)
        .map((step) => [step.childScope, step.id]),
    );
    const grouped = new Map<string | undefined, ExecutionStep[]>();
    for (const step of execution.steps) {
      const parent = step.parentId ?? scopes.get(step.scope);
      const siblings = grouped.get(parent) ?? [];
      siblings.push(step);
      grouped.set(parent, siblings);
    }
    return grouped;
  }, [execution.steps]);
  function renderStep(step: ExecutionStep) {
    const { label, icon: Icon } = appearance[step.kind];
    return (
      <li key={step.id} className={styles.step}>
        <Tooltip>
          <TooltipTrigger
            render={<span tabIndex={0} />}
            className={styles.stepRow}
          >
            <Icon size={13} aria-hidden="true" />
            <span className={styles.stepLabel}>{t(label)}</span>
            {step.name && (
              <span className={styles.stepName}>
                {step.kind === "hitl" && step.name === "Approval"
                  ? t("Approval")
                  : step.name}
              </span>
            )}
            <span
              className={styles.statusDot}
              data-state={step.state}
              aria-label={
                step.dispatchOnly
                  ? t("Dispatched")
                  : t(`state.${step.state}`, {
                      defaultValue: step.state,
                    })
              }
            />
          </TooltipTrigger>
          <TooltipPopup
            role="tooltip"
            side="left"
            sideOffset={16}
            className={styles.preview}
          >
            <div className={styles.previewContent}>
              <div className={styles.runTitle}>
                <strong>{t(label)}</strong>
                <StatePill
                  state={step.state}
                  label={step.dispatchOnly ? t("Dispatched") : undefined}
                />
              </div>
              {step.name && <p className={styles.previewInput}>{step.name}</p>}
              {step.dispatchOnly && (
                <p className={styles.itemsExplanation}>
                  {t(
                    "Delegation was dispatched. Child progress is tracked in its own thread.",
                  )}
                </p>
              )}
              <StepPreview step={step} execution={execution} />
            </div>
          </TooltipPopup>
        </Tooltip>
        {children.has(step.id) && (
          <ul>{children.get(step.id)!.map(renderStep)}</ul>
        )}
      </li>
    );
  }
  return (
    <>
      <ul>{children.get(undefined)?.map(renderStep)}</ul>
      {execution.steps.length === 0 && (
        <p>{t("No execution actions recorded.")}</p>
      )}
      {!!execution.observations?.length && (
        <details className={styles.observations}>
          <summary>
            {t("Other events")}{" "}
            <span className={styles.count}>
              {execution.observations.length}
            </span>
          </summary>
          <p className={styles.itemsExplanation}>
            {t("Supporting observations are not counted as steps.")}
          </p>
          {execution.observations.map((event) => (
            <Tooltip key={event.id}>
              <TooltipTrigger
                render={<span tabIndex={0} />}
                className={styles.observationRow}
              >
                {t(event.name, {
                  defaultValue: event.name.replaceAll("_", " "),
                })}
              </TooltipTrigger>
              <TooltipPopup
                role="tooltip"
                side="left"
                sideOffset={16}
                className={styles.preview}
              >
                <div className={styles.previewContent}>
                  <strong>{event.name}</strong>
                  <pre className={styles.previewData}>
                    {asText(event.detail)}
                  </pre>
                </div>
              </TooltipPopup>
            </Tooltip>
          ))}
        </details>
      )}
    </>
  );
}

function StepPreview({
  step,
  execution,
}: {
  step: ExecutionStep;
  execution: Execution;
}) {
  const { t } = useTranslation();
  const items = step.items.flatMap((id) => execution.items.get(id) ?? []);
  const detail = step.detail;
  return (
    <>
      {items.map((item) => (
        <div key={item.id} className={styles.previewSection}>
          {item.kind === "tool_call" ? (
            <>
              {step.kind === "llm" && (
                <>
                  <span className={styles.eyebrow}>{t("Tool request")}</span>
                  <p className={styles.previewInput}>{item.toolName}</p>
                </>
              )}
              <span className={styles.eyebrow}>{t("Arguments")}</span>
              <pre className={styles.previewData}>{asText(item.arguments)}</pre>
              {step.kind !== "llm" && item.result !== undefined && (
                <>
                  <span className={styles.eyebrow}>{t("Result")}</span>
                  <pre className={styles.previewData}>
                    {asText(item.result)}
                  </pre>
                </>
              )}
            </>
          ) : (
            <>
              <span className={styles.eyebrow}>
                {t(
                  item.kind === "reasoning_message"
                    ? "Reasoning summary"
                    : "Agent reply",
                )}
              </span>
              <p className={styles.previewInput}>
                {item.text || t("No text content")}
              </p>
            </>
          )}
          {step.kind !== "llm" && item.failure !== undefined && (
            <pre className={styles.previewData}>{asText(item.failure)}</pre>
          )}
        </div>
      ))}
      {detail !== undefined && (
        <div className={styles.previewSection}>
          <span className={styles.eyebrow}>{t("Execution details")}</span>
          {isObject(detail) && typeof detail.summary === "string" ? (
            <p className={styles.previewInput}>{detail.summary}</p>
          ) : (
            <pre className={styles.previewData}>{asText(detail)}</pre>
          )}
        </div>
      )}
      {items.length === 0 && detail === undefined && (
        <p className={styles.itemsExplanation}>
          {t("No additional details recorded.")}
        </p>
      )}
    </>
  );
}
function asText(value: unknown): string {
  return (
    typeof value === "string" ? value : (JSON.stringify(value, null, 2) ?? "")
  ).slice(0, 2400);
}
