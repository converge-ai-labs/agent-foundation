import { Button, Menu, MenuItem, MenuPopup, MenuTrigger } from "a13n-ui";
import {
  CaretDownIcon,
  CaretLeftIcon,
  CaretRightIcon,
} from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../../../layout/workspace";
import type { Schema } from "../../../../shared/api";
import { useAgent } from "../../../agents/queries";
import { runPath } from "../../api";
import { formatDuration, resultExcerpt } from "../../format";
import { runRequest } from "../../request";
import { useChildThreads, useThreadRuns } from "../thread-runs";
import { runSection } from "./view";
import styles from "./details.module.css";

type Run = Schema["RunResource"];

/**
 * Where the reader is in the Thread, and every other Run they can go to,
 * including the Runs of Threads this one delegated to.
 */
export function RunNavigator({
  thread,
  runId,
}: {
  thread: Schema["ThreadResource"];
  runId: string;
}) {
  const { t } = useTranslation();
  const { basePath } = useWorkspace();
  const navigate = useNavigate();
  const { runs } = useThreadRuns(thread.id);
  const children = useChildThreads(thread.session_id, thread.id);
  const inView = useVisibleRun(runId);
  if (runs.length <= 1 && !children.length) return null;
  const position = runs.findIndex((run) => run.id === inView);
  const open = (run: Run) => {
    // A section already on the page is reached by scrolling, not by navigating.
    const loaded = runSection(run.id);
    if (loaded) loaded.scrollIntoView({ block: "start", behavior: "smooth" });
    else
      navigate(
        runPath(
          basePath,
          {
            session_id: run.session_id,
            thread_id: run.thread_id,
            run_id: run.id,
          },
          "debug",
        ),
      );
  };
  const step = (offset: number) => {
    const next = runs[position + offset];
    if (next) open(next);
  };
  return (
    <div className={styles.navigator}>
      <div className={styles.navigatorPill}>
        <Button
          size="icon-sm"
          variant="ghost"
          type="button"
          aria-label={t("Previous run")}
          title={t("Previous run")}
          disabled={position <= 0}
          onClick={() => step(-1)}
        >
          <CaretLeftIcon size={13} aria-hidden="true" />
        </Button>
        <Menu>
          <MenuTrigger
            render={
              <Button
                variant="ghost"
                size="sm"
                type="button"
                className={styles.navigatorLabel}
              />
            }
          >
            {position < 0
              ? t("Runs")
              : t("Run {{index}} of {{total}}", {
                  index: position + 1,
                  total: runs.length,
                })}
            <CaretDownIcon size={11} aria-hidden="true" />
          </MenuTrigger>
          <MenuPopup align="center" className={styles.navigatorPopup}>
            {runs.map((run, index) => (
              <div key={run.id}>
                <NavigatorItem
                  run={run}
                  thread={thread}
                  label={t("Run {{index}}", { index: index + 1 })}
                  current={run.id === inView}
                  onSelect={() => open(run)}
                />
                {children
                  .filter((child) => child.thread.origin_run_id === run.id)
                  .map((child) => (
                    <ChildRuns
                      key={child.thread.id}
                      child={child}
                      parentAgentId={run.agent_id}
                      currentRunId={inView}
                      onSelect={open}
                    />
                  ))}
              </div>
            ))}
          </MenuPopup>
        </Menu>
        <Button
          size="icon-sm"
          variant="ghost"
          type="button"
          aria-label={t("Next run")}
          title={t("Next run")}
          disabled={position < 0 || position >= runs.length - 1}
          onClick={() => step(1)}
        >
          <CaretRightIcon size={13} aria-hidden="true" />
        </Button>
      </div>
    </div>
  );
}

/** A child Thread is named by the Agent that answers it, when that differs. */
function ChildRuns({
  child,
  parentAgentId,
  currentRunId,
  onSelect,
}: {
  child: { thread: Schema["ThreadResource"]; runs: readonly Run[] };
  parentAgentId?: string;
  currentRunId: string | null;
  onSelect: (run: Run) => void;
}) {
  const { t } = useTranslation();
  const agentId = child.runs[0]?.agent_id;
  const agent = useAgent(
    agentId && agentId !== parentAgentId ? agentId : undefined,
  );
  return (
    <div className={styles.navigatorChild}>
      <span className={styles.navigatorChildLabel}>
        {agent.data?.name ?? t("Child thread")}
      </span>
      {child.runs.map((run, index) => (
        <NavigatorItem
          key={run.id}
          run={run}
          thread={child.thread}
          label={t("Run {{index}}", { index: index + 1 })}
          current={run.id === currentRunId}
          onSelect={() => onSelect(run)}
        />
      ))}
    </div>
  );
}

function NavigatorItem({
  run,
  thread,
  label,
  current,
  onSelect,
}: {
  run: Run;
  thread: Schema["ThreadResource"];
  label: string;
  current: boolean;
  onSelect: () => void;
}) {
  const { t } = useTranslation();
  // The excerpt is the same resolved request the Run's own section shows.
  const excerpt = resultExcerpt(runRequest(run, thread).text, 64);
  const duration =
    run.started_at && run.completed_at
      ? formatDuration(
          Date.parse(run.completed_at) - Date.parse(run.started_at),
        )
      : t(`state.${run.status}`, { defaultValue: run.status });
  return (
    <MenuItem
      className={styles.navigatorItem}
      data-current={current || undefined}
      onClick={onSelect}
    >
      <span
        className={styles.navigatorDot}
        data-state={run.status}
        aria-hidden="true"
      />
      <span className={styles.navigatorName}>{label}</span>
      <span className={styles.navigatorExcerpt}>
        {excerpt || t("No request text")}
      </span>
      <span className={styles.navigatorMeta}>{duration}</span>
    </MenuItem>
  );
}

/** Which loaded Run section the reader is actually looking at. */
function useVisibleRun(fallback: string) {
  const [visible, setVisible] = useState<string | null>(null);
  useEffect(() => {
    const stage = document.querySelector("[data-session-stage]");
    if (!(stage instanceof HTMLElement)) return;
    if (typeof IntersectionObserver === "undefined") return;
    const seen = new Map<string, number>();
    const observer = new IntersectionObserver(
      (records) => {
        for (const record of records) {
          const id = (record.target as HTMLElement).dataset.run;
          if (id) seen.set(id, record.intersectionRatio);
        }
        const sections = [...stage.querySelectorAll<HTMLElement>("[data-run]")];
        const top = sections.find(
          (section) => (seen.get(section.dataset.run ?? "") ?? 0) > 0,
        );
        if (top?.dataset.run) setVisible(top.dataset.run);
      },
      { root: stage, threshold: [0, 0.01, 0.5] },
    );
    const attach = () => {
      for (const section of stage.querySelectorAll("[data-run]"))
        observer.observe(section);
    };
    attach();
    const mutations = new MutationObserver(attach);
    mutations.observe(stage, { childList: true, subtree: true });
    return () => {
      observer.disconnect();
      mutations.disconnect();
    };
  }, []);
  return visible ?? fallback;
}
