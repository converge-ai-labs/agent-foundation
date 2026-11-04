import { useTranslation } from "../i18n";
import { HelpTip } from "../shell/help-tip";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import {
  Button,
  Dialog,
  DialogPopup,
  Skeleton,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuItem,
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
import { createPortal } from "react-dom";
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

const activityViews = {
  tasks: { Icon: ListChecks },
  notes: { Icon: Notebook },
  children: { Icon: UsersThree },
  processes: { Icon: Terminal },
  tools: { Icon: Wrench },
};
type ActivityView = keyof typeof activityViews;

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
  status,
  connectionStatus,
  panel,
}: {
  panel?: HTMLElement | null;
  status?: ReactNode;
  connectionStatus?: ReactNode;
  threadId: string;
  display: FocusDisplay;
  connected?: boolean;
  reconcile: () => void;
  goal?: Schema<"GoalView"> | null;
  activity?: Schema<"RootActivityView">;
  onUseGoal?: () => void;
}) {
  const { t } = useTranslation();
  const labels: Record<ActivityView, string> = {
    tasks: t("Tasks"),
    notes: t("Notes"),
    children: t("Subagents"),
    processes: t("Processes"),
    tools: t("Tools"),
  };
  const guest = useGuestAccess();
  const debug = useDebug();
  const [tab, setTab] = useState<"goal" | ActivityView | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const moreTrigger = useRef<HTMLButtonElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const titleId = useId();
  const [docked, setDocked] = useState(false);
  const open = tab !== null && tab !== "goal";
  useEffect(() => {
    const workspace = panel?.parentElement;
    if (!workspace) return;
    const update = () =>
      setDocked(workspace.getBoundingClientRect().width >= 900);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(workspace);
    return () => observer.disconnect();
  }, [panel]);
  useEffect(() => {
    if (docked && open) heading.current?.focus();
  }, [docked, open]);
  const close = () => {
    setTab(null);
    if (docked) returnFocus.current?.focus();
  };
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
  const childActive = summary?.children.active ?? 0;
  const processes = display.processes.background;
  const running = processes.filter((item) => item.phase === "running").length;
  const incomplete =
    !connected ||
    display.gap ||
    [...display.children.values()].some((child) => child.display.gap);
  const counts: Partial<Record<ActivityView, number | string>> = {
    tasks: total ? `${complete}/${total}` : undefined,
    notes: summary?.notes.total,
    children: childActive,
    processes: running,
  };
  const tabs = (
    ["tasks", "notes", "children", "processes", "tools"] as const
  ).filter((key) => !guest || (key !== "processes" && key !== "tools"));
  const key = tab && tab !== "goal" ? tab : "tasks";
  const content = (
    <>
      <div className={styles.heading}>
        <h2 id={titleId} ref={heading} tabIndex={-1}>
          {t("Chat activity")}
        </h2>
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
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={close}
          aria-label={t("Close activity")}
        >
          <X />
        </Button>
      </div>
      <div className={styles.activityLayout}>
        <div className={styles.tabs} aria-label={t("Activity views")}>
          {tabs.map((item) => {
            const { Icon } = activityViews[item];
            const label = labels[item];
            const count = counts[item];
            return (
              <Button
                key={item}
                variant="ghost"
                size="sm"
                aria-label={t("Inspect {{activity}}", {
                  activity: label.toLowerCase(),
                })}
                aria-pressed={key === item}
                onClick={() => setTab(item)}
              >
                <Icon />
                {label}
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
                <Loading label={t("Loading tasks")} />
              )}
              {!saved.isPending && !page && (
                <p>{t("Task state unavailable.")}</p>
              )}
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
              {work.notes.isPending && <Loading label={t("Loading notes")} />}
              {!work.notes.isPending && !work.notes.data?.notes.page && (
                <p>{t("Notes unavailable.")}</p>
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
                    {t("{{active}} active · {{unavailable}} unavailable", {
                      active: summary.children.active ?? 0,
                      unavailable: summary.children.unavailable ?? 0,
                    })}
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
    </>
  );
  return (
    <div className={styles.strip} aria-label={t("Work inspection")}>
      {(goal || active) && (
        <div className={styles.summary}>
          {goal && (
            <GoalStatus
              goal={goal}
              activity={activity}
              open={tab === "goal"}
              onOpenChange={(open) =>
                setTab((current) =>
                  open ? "goal" : current === "goal" ? null : current,
                )
              }
              onUseObjective={onUseGoal}
            />
          )}
          {active && (
            <span
              className={styles.active}
              title={active.active_form || active.subject}
            >
              <TaskIcon status={active.status} />
              <span>{active.active_form || active.subject}</span>
            </span>
          )}
        </div>
      )}
      <Dialog
        open={open && !docked}
        onOpenChange={(open) => {
          if (!open) setTab((current) => (current === "goal" ? current : null));
        }}
      >
        <div className={styles.shortcuts}>
          {tabs.map((item) => {
            const { Icon } = activityViews[item];
            const label = labels[item];
            return (
              <Button
                key={item}
                variant="ghost"
                size="sm"
                aria-expanded={tab === item}
                aria-haspopup={docked ? undefined : "dialog"}
                className={
                  item === "tasks" || item === "children"
                    ? undefined
                    : styles.secondaryShortcut
                }
                aria-label={t("Inspect {{activity}}", {
                  activity: label.toLowerCase(),
                })}
                title={
                  item === "children"
                    ? t("Subagents · active executions")
                    : item === "processes"
                      ? t("Processes · observed running processes")
                      : undefined
                }
                onClick={(event) => {
                  returnFocus.current = event.currentTarget;
                  setTab(docked && tab === item ? null : item);
                }}
              >
                <Icon />
                {label}
                {!!counts[item] && (
                  <span className={styles.count}>{counts[item]}</span>
                )}
              </Button>
            );
          })}
          <Menu>
            <MenuTrigger
              ref={moreTrigger}
              render={<Button variant="ghost" size="sm" />}
              className={styles.more}
              aria-label={t("More activity")}
            >
              {t("Activity")}
              {!guest && running > 0 && (
                <span
                  className={styles.backgroundActivity}
                  aria-label={t("{{count}} observed running processes", {
                    count: running,
                  })}
                  title={t("{{count}} observed running processes", {
                    count: running,
                  })}
                >
                  <Terminal />
                  <span>{running}</span>
                </span>
              )}
              <CaretDown />
            </MenuTrigger>
            <MenuPopup
              align="end"
              finalFocus={tab !== null ? false : undefined}
            >
              {tabs.map((item) => {
                const { Icon } = activityViews[item];
                const label = labels[item];
                return (
                  <MenuItem
                    key={item}
                    onClick={() => {
                      returnFocus.current = moreTrigger.current;
                      setTab(item);
                    }}
                  >
                    <Icon />
                    {label}
                    {!!counts[item] && (
                      <span className={styles.count}>{counts[item]}</span>
                    )}
                  </MenuItem>
                );
              })}
            </MenuPopup>
          </Menu>
          <div className={styles.connection}>{connectionStatus}</div>
          {status && <div className={styles.status}>{status}</div>}
        </div>
        {!docked && (
          <DialogPopup
            aria-labelledby={titleId}
            showCloseButton={false}
            className={styles.popup}
            initialFocus={heading}
            finalFocus={() => (tab === "goal" ? false : returnFocus.current)}
          >
            {content}
          </DialogPopup>
        )}
      </Dialog>
      {docked &&
        open &&
        panel &&
        createPortal(
          <aside
            className={styles.docked}
            aria-labelledby={titleId}
            onKeyDown={(event) => {
              if (event.key === "Escape" && !event.defaultPrevented) {
                event.stopPropagation();
                close();
              }
            }}
          >
            {content}
          </aside>,
          panel,
        )}
    </div>
  );
}
