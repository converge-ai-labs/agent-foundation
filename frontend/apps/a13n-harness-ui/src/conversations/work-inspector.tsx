import { HelpTip } from "../shell/help-tip";
import { useEffect, useState } from "react";
import {
  Button,
  Dialog,
  DialogTrigger,
  DialogPopup,
  DialogTitle,
  DialogClose,
  Skeleton,
} from "a13n-ui";
import {
  ListChecks,
  Notebook,
  UsersThree,
  Terminal,
  Wrench,
  X,
  CaretDown,
} from "@phosphor-icons/react";
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
  const guest = useGuestAccess();
  const debug = useDebug();
  const [tab, setTab] = useState<
    "goal" | "tasks" | "notes" | "children" | "processes" | "tools" | null
  >(null);
  useEffect(() => {
    if (!goal && tab === "goal") setTab(null);
  }, [goal, tab]);
  const work = useThreadWork(threadId, {
    tasks: tab === "tasks",
    notes: tab === "notes",
  });
  const saved = work.tasks;
  const page = work.tasks.data?.tasks.page ?? undefined;
  const summary = work.summary.data;
  const active = summary?.tasks.active;
  const complete = summary?.tasks.completed ?? 0;
  const total = summary?.tasks.total ?? 0;
  const childTotal = summary
    ? [
        summary.children.running,
        summary.children.succeeded,
        summary.children.failed,
        summary.children.cancelled,
        summary.children.lost,
      ].reduce<number>((sum, count) => sum + (count ?? 0), 0)
    : 0;
  const processes = display.processes.background;
  const running = processes.filter((item) => item.phase === "running").length;
  const incomplete =
    !connected ||
    display.gap ||
    [...display.children.values()].some((child) => child.display.gap);
  const labels = {
    tasks: "Tasks",
    notes: "Notes",
    children: "Subagents",
    processes: "Processes",
    tools: "Tools",
  };
  const tabs = (
    ["tasks", "notes", "children", "processes", "tools"] as const
  ).filter((key) => !guest || (key !== "processes" && key !== "tools"));
  const key = tab && tab !== "goal" ? tab : "tasks";
  return (
    <div className={styles.strip} aria-label="Work inspection">
      {goal && (
        <GoalStatus
          goal={goal}
          activity={activity}
          open={tab === "goal"}
          onOpenChange={(open) => setTab(open ? "goal" : null)}
          onUseObjective={onUseGoal}
        />
      )}
      <Dialog
        open={tab !== null && tab !== "goal"}
        onOpenChange={(open) => setTab(open ? "tasks" : null)}
      >
        <DialogTrigger
          render={<Button variant="ghost" size="sm" />}
          aria-label="Chat activity"
        >
          <ListChecks />
          Activity
          {!!total && (
            <span className={styles.count}>
              {complete}/{total}
            </span>
          )}
          <CaretDown />
        </DialogTrigger>
        <DialogPopup showCloseButton={false} className={styles.popup}>
          <div className={styles.heading}>
            <DialogTitle>Chat activity</DialogTitle>
            <HelpTip label="About activity">
              {key === "processes" &&
                "Recent processes from this chat and its subagents. Other server processes are not shown. "}
              {workCaption(
                key === "notes" ? work.notes.data : work.tasks.data,
                work.stale || !connected,
                work.summary.isFetching,
                debug.enabled,
              )}
            </HelpTip>
            <DialogClose
              render={<Button variant="ghost" size="icon-sm" />}
              aria-label="Close activity"
            >
              <X />
            </DialogClose>
          </div>
          <div className={styles.activityLayout}>
            <div className={styles.tabs} aria-label="Activity views">
              {tabs.map((item) => {
                const Icon =
                  item === "tasks"
                    ? ListChecks
                    : item === "notes"
                      ? Notebook
                      : item === "children"
                        ? UsersThree
                        : item === "tools"
                          ? Wrench
                          : Terminal;
                const count =
                  item === "children"
                    ? childTotal
                    : item === "notes"
                      ? summary?.notes.total
                      : item === "processes"
                        ? running
                        : undefined;
                return (
                  <Button
                    key={item}
                    variant="ghost"
                    size="sm"
                    aria-label={`Inspect ${labels[item].toLowerCase()}`}
                    aria-pressed={key === item}
                    onClick={() => setTab(item)}
                  >
                    <Icon />
                    {labels[item]}
                    {!!count && <span className={styles.count}>{count}</span>}
                  </Button>
                );
              })}
            </div>
            <div className={styles.inspectorBody}>
              {key !== "tools" && (
                <h3 className={styles.panelTitle}>{labels[key]}</h3>
              )}
              {key === "tasks" && (
                <>
                  <ErrorNotice
                    error={saved.error}
                    retry={() => void saved.refetch()}
                  />
                  {!page && saved.isPending && (
                    <Loading label="Loading tasks" />
                  )}
                  {!saved.isPending && !page && <p>Task state unavailable.</p>}
                  <TaskList page={page} />
                </>
              )}
              {key === "tools" && tab === key && (
                <ToolExecutions threadId={threadId} />
              )}
              {key === "processes" && tab === key && (
                <Processes
                  observations={processes}
                  omitted={display.processes.omitted}
                  incomplete={incomplete}
                />
              )}
              {key === "notes" && tab === key && (
                <>
                  <ErrorNotice
                    error={work.notes.error}
                    retry={() => void work.notes.refetch()}
                  />
                  {work.notes.isPending && <Loading label="Loading notes" />}
                  {!work.notes.isPending && !work.notes.data?.notes.page && (
                    <p>Notes unavailable.</p>
                  )}
                  <NoteList page={work.notes.data?.notes.page} />
                </>
              )}
              {key === "children" && tab === key && (
                <>
                  {summary &&
                    ((summary.children.active ?? 0) > 0 ||
                      (summary.children.unavailable ?? 0) > 0) && (
                      <p className={styles.caption}>
                        {summary.children.active ?? 0} active ·{" "}
                        {summary.children.unavailable ?? 0} unavailable
                      </p>
                    )}
                  <Children
                    threadId={threadId}
                    reconcile={reconcile}
                    display={display}
                  />
                </>
              )}
            </div>
          </div>
        </DialogPopup>
      </Dialog>
      {active && (
        <span
          className={styles.active}
          title={active.active_form || active.subject}
        >
          <TaskIcon status={active.status} />
          {active.active_form || active.subject}
        </span>
      )}
    </div>
  );
}
