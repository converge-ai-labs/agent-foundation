import { useEffect, useState } from "react";
import { Skeleton } from "a13n-ui";
import { CaretRight } from "@phosphor-icons/react";
import { useTranslation } from "../i18n";
import { HelpTip } from "../shell/help-tip";
import { type Schema } from "../transport/client";
import { useGuestAccess } from "../transport/context";
import { useThreadWork, workCaption } from "./work";
import { useDebug } from "../shell/debug";
import { ErrorNotice } from "../shell/ui";
import { TaskIcon, TaskList, NoteList } from "./work-lists";
import { Children } from "./details";
import type { FocusDisplay } from "./stream";
import { Processes } from "./processes";
import { ToolExecutions } from "./tool-executions";
import { GoalStatus } from "./goal-status";
import styles from "./work-inspector.module.css";

type ActivityView = "tasks" | "notes" | "children" | "processes" | "tools";
function Loading({ label }: { label: string }) {
  return (
    <div role="status" aria-label={label} className={styles.loading}>
      <Skeleton className="h-4 w-3/4" />
      <Skeleton className="h-4 w-1/2" />
    </div>
  );
}

export function WorkInspector({
  threadId,
  display,
  connected = true,
  reconcile,
  goal,
  activity,
  onUseGoal,
}: {
  threadId: string;
  display: FocusDisplay;
  connected?: boolean;
  reconcile: () => void;
  goal?: Schema<"GoalView"> | null;
  activity?: Schema<"RootActivityView">;
  onUseGoal?: () => void;
}) {
  const { t } = useTranslation();
  const guest = useGuestAccess();
  const debug = useDebug();
  const [expanded, setExpanded] = useState<Set<ActivityView>>(
    () => new Set(["tasks"]),
  );
  const [goalOpen, setGoalOpen] = useState(false);
  useEffect(() => {
    if (!goal) setGoalOpen(false);
  }, [goal]);
  const work = useThreadWork(threadId, {
    tasks: expanded.has("tasks"),
    notes: expanded.has("notes"),
  });
  const summary = work.summary.data;
  const active = summary?.tasks.active;
  const processes = display.processes.background;
  const running = processes.filter((item) => item.phase === "running").length;
  const incomplete =
    !connected ||
    display.gap ||
    [...display.children.values()].some((child) => child.display.gap);
  const labels: Record<ActivityView, string> = {
    tasks: t("Tasks"),
    notes: t("Notes"),
    children: t("Subagents"),
    processes: t("Processes"),
    tools: t("Tool executions"),
  };
  const counts: Partial<Record<ActivityView, number | string>> = {
    tasks: summary
      ? `${summary.tasks.completed}/${summary.tasks.total}`
      : undefined,
    notes: summary?.notes.total,
    children: summary?.children.active,
    processes: running,
  };
  const sections = (
    ["tasks", "notes", "children", "processes", "tools"] as const
  ).filter((key) => !guest || (key !== "processes" && key !== "tools"));
  return (
    <div className={styles.inspector} aria-label={t("Work inspection")}>
      <div className={styles.currentWork}>
        <span className={styles.caption}>
          {!activity
            ? t("Unavailable")
            : activity.state === "inactive"
              ? t("Inactive")
              : activity.state === "preparing"
                ? t("Preparing")
                : t("Working")}
        </span>
        {active && (
          <p className={styles.currentTask}>
            <TaskIcon status={active.status} />
            <span>{active.active_form || active.subject}</span>
          </p>
        )}
        {goal && (
          <GoalStatus
            goal={goal}
            activity={activity}
            open={goalOpen}
            onOpenChange={setGoalOpen}
            onUseObjective={onUseGoal}
          />
        )}
      </div>
      <ErrorNotice
        error={work.summary.error}
        retry={() => void work.summary.refetch()}
      />
      {sections.map((key) => (
        <section key={key} className={styles.group}>
          <div className={styles.groupHeader}>
            <button
              aria-expanded={expanded.has(key)}
              onClick={() =>
                setExpanded((previous) => {
                  const next = new Set(previous);
                  if (next.has(key)) next.delete(key);
                  else next.add(key);
                  return next;
                })
              }
            >
              <CaretRight aria-hidden />
              <span>{labels[key]}</span>
              {counts[key] !== undefined && (
                <span className={styles.count}>{counts[key]}</span>
              )}
            </button>
            <HelpTip label={t("About activity")}>
              {key === "processes" &&
                t(
                  "Recent processes from this chat and its subagents. Other server processes are not shown. ",
                )}
              {workCaption(
                t,
                key === "notes" ? work.notes.data : work.tasks.data,
                work.stale || !connected,
                work.summary.isFetching,
                debug.enabled,
              )}
            </HelpTip>
          </div>
          {expanded.has(key) && (
            <div className={styles.groupBody}>
              {key === "tasks" && (
                <>
                  <ErrorNotice
                    error={work.tasks.error}
                    retry={() => void work.tasks.refetch()}
                  />
                  {work.tasks.isPending && (
                    <Loading label={t("Loading tasks")} />
                  )}
                  {!work.tasks.isPending && !work.tasks.data?.tasks.page && (
                    <p>{t("Task state unavailable.")}</p>
                  )}
                  <TaskList page={work.tasks.data?.tasks.page ?? undefined} />
                </>
              )}
              {key === "notes" && (
                <>
                  <ErrorNotice
                    error={work.notes.error}
                    retry={() => void work.notes.refetch()}
                  />
                  {work.notes.isPending && (
                    <Loading label={t("Loading notes")} />
                  )}
                  {!work.notes.isPending && !work.notes.data?.notes.page && (
                    <p>{t("Notes unavailable.")}</p>
                  )}
                  <NoteList page={work.notes.data?.notes.page} />
                </>
              )}
              {key === "children" && (
                <Children
                  threadId={threadId}
                  reconcile={reconcile}
                  display={display}
                />
              )}
              {key === "processes" && (
                <Processes
                  observations={processes}
                  omitted={display.processes.omitted}
                  incomplete={incomplete}
                />
              )}
              {key === "tools" && <ToolExecutions threadId={threadId} />}
            </div>
          )}
        </section>
      ))}
    </div>
  );
}
