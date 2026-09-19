import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";
import { Button, Tooltip, TooltipTrigger, TooltipPopup } from "a13n-ui";
import { CaretRightIcon, GitBranchIcon } from "@phosphor-icons/react";
import { useWorkspace } from "../../../layout/workspace";
import type { Schema } from "../../../shared/api";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../../shared/feedback";
import { runPath } from "../api";
import { useExecution } from "../use-execution";
import { MapExecution } from "./session-map-execution";
import { inputText } from "../input";
import styles from "./session-map.module.css";

export function MapRun({
  run,
  index,
  children,
  branchPoint = false,
}: {
  run: Schema["RunResource"];
  index: number;
  children?: ReactNode;
  branchPoint?: boolean;
}) {
  const { t } = useTranslation();
  const { runId } = useParams();
  const { basePath } = useWorkspace();
  const [expanded, setExpanded] = useState(false);
  const { state, retry } = useExecution(run.id, expanded);
  const execution = "execution" in state ? state.execution : undefined;
  const label = `${t("Run")} ${index}`;
  const input =
    inputText(run.input, run.input_text).trim() ||
    (run.input ? JSON.stringify(run.input, null, 2) : t("No request text"));
  return (
    <li className={styles.runNode} data-branch-point={branchPoint || undefined}>
      <div
        className={styles.runRow}
        data-current={run.id === runId || undefined}
      >
        <Button
          variant="ghost"
          size="icon-sm"
          className={styles.expand}
          aria-label={t("Inspect steps for {{run}}", { run: label })}
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          <CaretRightIcon
            size={12}
            className={styles.caret}
            data-expanded={expanded}
          />
        </Button>
        <Tooltip>
          <TooltipTrigger
            render={
              <Link
                to={runPath(basePath, {
                  session_id: run.session_id,
                  thread_id: run.thread_id,
                  run_id: run.id,
                })}
              />
            }
            className={styles.runLink}
            aria-label={`${label} · ${t(`state.${run.status}`, { defaultValue: run.status })} · ${input.slice(0, 160)}`}
            aria-current={run.id === runId ? "page" : undefined}
          >
            <span
              className={styles.statusDot}
              data-state={run.status}
              aria-hidden="true"
            />
            <span className={styles.runLabel}>{label}</span>
            <span className={styles.runExcerpt}>{input}</span>
            {branchPoint && (
              <span className={styles.branchPoint}>
                <GitBranchIcon size={12} />
                <span className="sr-only">{t("Branch point")}</span>
              </span>
            )}
            {expanded && execution && (
              <span className={styles.count}>
                {t("{{count}} steps", { count: execution.steps.length })}
              </span>
            )}
          </TooltipTrigger>
          <TooltipPopup
            role="tooltip"
            side="left"
            align="center"
            sideOffset={16}
            className={styles.preview}
          >
            <div className={styles.previewContent}>
              <div className={styles.runTitle}>
                <strong>{label}</strong>
                <StatePill state={run.status} />
              </div>
              {branchPoint && (
                <p className={styles.branchExplanation}>
                  {t(
                    "Child threads branch from this run. It stays visible when earlier runs are collapsed.",
                  )}
                </p>
              )}
              <span className={styles.eyebrow}>{t("Input")}</span>
              <p className={styles.previewInput}>{input}</p>
              <div className={styles.previewFooter}>
                <Timestamp value={run.created_at} />
                <span>
                  {t(`trigger.${run.trigger_type}`, {
                    defaultValue: run.trigger_type,
                  })}
                </span>
              </div>
              <code>{run.id}</code>
            </div>
          </TooltipPopup>
        </Tooltip>
      </div>
      {expanded && (
        <div className={styles.items}>
          {state.status === "error" && (
            <ErrorNotice error={state.error} retry={retry} />
          )}
          {state.status === "loading" && <Loading variant="list" rows={3} />}
          {state.status === "unavailable" ? (
            <p>{t("Execution history is unavailable or incomplete.")}</p>
          ) : execution ? (
            <MapExecution execution={execution} />
          ) : null}
        </div>
      )}
      {children}
    </li>
  );
}
