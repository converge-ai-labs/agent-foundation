import {
  CaretRightIcon,
  CheckIcon,
  CircleNotchIcon,
  CubeIcon,
  SparkleIcon,
  WrenchIcon,
} from "@phosphor-icons/react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { StatePill } from "../../../shared/feedback";
import { JsonView } from "../../../shared/forms";
import { MarkdownContent } from "../../../shared/markdown";
import { parseItemValue, type PresentedItem } from "../projection";
import { executionState, executionSummary, humanKind } from "./items";
import styles from "./transcript.module.css";

/** One step of execution: what ran, what it was about, and how it ended. */
export function ExecutionRow({
  item,
  runState,
}: {
  item: PresentedItem;
  runState?: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const state = executionState(item, runState);
  const summary = executionSummary(item);
  const reasoning = item.kind === "reasoning_message";
  const tool = item.kind === "tool_call";
  const name = tool
    ? item.toolName || t("Tool call")
    : reasoning
      ? t("Reasoning")
      : humanKind(item.kind);
  return (
    <div className={styles.executionRow} data-message-id={item.id}>
      <button
        type="button"
        className={styles.executionTrigger}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <CaretRightIcon
          size={12}
          aria-hidden="true"
          className={styles.caret}
          data-expanded={open || undefined}
        />
        <span className={styles.executionIcon} aria-hidden="true">
          {tool ? (
            <WrenchIcon size={13} />
          ) : reasoning ? (
            <SparkleIcon size={13} />
          ) : (
            <CubeIcon size={13} />
          )}
        </span>
        <strong className={styles.executionName} title={name}>
          {name}
        </strong>
        {summary && (
          <span className={styles.executionSummary} title={summary}>
            {summary}
          </span>
        )}
        <span className={styles.executionState}>
          {state.affordance === "done" ? (
            <CheckIcon size={13} aria-label={t("Completed")} />
          ) : state.affordance === "working" ? (
            <CircleNotchIcon
              size={13}
              className={styles.spinning}
              aria-label={t("Working")}
            />
          ) : (
            <StatePill state={state.state} />
          )}
        </span>
      </button>
      {open && (
        <div className={styles.executionBody}>
          {reasoning ? (
            <>
              {item.text && (
                <div className={styles.reasoningProse}>
                  <MarkdownContent text={item.text} />
                </div>
              )}
              {item.protectedReasoning && (
                <p className={styles.executionNote}>
                  {t("The provider retained protected reasoning content.")}
                </p>
              )}
            </>
          ) : tool ? (
            <>
              <h4>{t("Arguments")}</h4>
              <JsonView value={parseItemValue(item.arguments)} />
              {item.result !== undefined && (
                <>
                  <h4>{t("Result")}</h4>
                  <JsonView value={parseItemValue(item.result)} />
                </>
              )}
            </>
          ) : (
            <JsonView value={item.detail} />
          )}
          {item.failure !== undefined && (
            <>
              <h4>{t("Error details")}</h4>
              <JsonView value={item.failure} />
            </>
          )}
        </div>
      )}
    </div>
  );
}
