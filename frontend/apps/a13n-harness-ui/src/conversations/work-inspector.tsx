import { useBackground } from "./background-context";
import { useTranslation } from "../i18n";
import { HelpTip } from "../shell/help-tip";
import { ThreadBotNote } from "../bot-notes";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Button,
  Dialog,
  DialogTrigger,
  DialogPopup,
  DialogTitle,
  DialogClose,
  Skeleton,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuItem,
} from "a13n-ui";
import { ListChecks, Notebook, X, DotsThree } from "@phosphor-icons/react";
import { type Schema } from "../transport/client";
import { useGuestAccess } from "../transport/context";
import { useThreadWork, workCaption } from "./work";
import { useDebug } from "../shell/debug";
import { ErrorNotice } from "../shell/ui";
import { TaskList, NoteList } from "./work-lists";
import { GoalStatus } from "./goal-status";
import styles from "./work-inspector.module.css";

const activityViews = {
  tasks: { Icon: ListChecks },
  notes: { Icon: Notebook },
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
  connected = true,
  goal,
  activity,
  onUseGoal,
  status,
  connectionStatus,
  navigation,
  showDetails = true,
}: {
  showDetails?: boolean;
  status?: ReactNode;
  connectionStatus?: ReactNode;
  navigation?: ReactNode;
  threadId: string;
  connected?: boolean;
  goal?: Schema<"GoalView"> | null;
  activity?: Schema<"RootActivityView">;
  onUseGoal?: () => void;
}) {
  const { t } = useTranslation();
  const labels: Record<ActivityView, string> = {
    tasks: t("Tasks"),
    notes: t("Notes"),
  };
  const guest = useGuestAccess();
  const debug = useDebug();
  const [tab, setTab] = useState<"goal" | ActivityView | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const moreTrigger = useRef<HTMLButtonElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
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
  const complete = summary?.tasks.completed ?? 0;
  const total = summary?.tasks.total ?? 0;
  const counts: Partial<Record<ActivityView, number | string>> = {
    tasks: total ? `${complete}/${total}` : undefined,
    notes: summary?.notes.total,
  };
  const hasActivityCard = !!useBackground().threadId;
  const tabs = ["tasks", "notes"] as const;
  const key = tab && tab !== "goal" ? tab : "tasks";
  return (
    <div className={styles.strip} aria-label={t("Work inspection")}>
      {showDetails && goal && (
        <div className={styles.summary}>
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
        </div>
      )}
      <Dialog
        open={tab !== null && tab !== "goal"}
        onOpenChange={(open) => {
          if (!open) setTab((current) => (current === "goal" ? current : null));
        }}
      >
        <div className={styles.shortcuts}>
          {navigation}
          {showDetails &&
            !hasActivityCard &&
            tabs.map((item) => {
              const { Icon } = activityViews[item];
              const label = labels[item];
              return (
                <DialogTrigger
                  key={item}
                  render={<Button variant="ghost" size="sm" />}
                  className={styles.shortcut}
                  aria-label={t("Inspect {{activity}}", {
                    activity: label.toLowerCase(),
                  })}
                  onClick={(event) => {
                    returnFocus.current = event.currentTarget;
                    setTab(item);
                  }}
                >
                  <Icon />
                  {!!counts[item] && (
                    <span className={styles.count}>{counts[item]}</span>
                  )}
                </DialogTrigger>
              );
            })}
          {showDetails && !hasActivityCard && (
            <Menu>
              <MenuTrigger
                ref={moreTrigger}
                render={<Button variant="ghost" size="sm" />}
                className={styles.more}
                aria-label={t("More activity")}
              >
                <DotsThree />
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
          )}
          <div className={styles.connection}>{connectionStatus}</div>
          {status && <div className={styles.status}>{status}</div>}
        </div>
        <DialogPopup
          showCloseButton={false}
          className={styles.popup}
          initialFocus={heading}
          finalFocus={() => (tab === "goal" ? false : returnFocus.current)}
        >
          <div className={styles.heading}>
            <DialogTitle ref={heading} tabIndex={-1}>
              {t("Chat activity")}
            </DialogTitle>
            <HelpTip label={t("About activity")}>
              {workCaption(
                t,
                key === "notes" ? work.notes.data : work.tasks.data,
                work.stale || !connected,
                work.summary.isFetching,
                debug.enabled,
              )}
            </HelpTip>
            <DialogClose
              render={<Button variant="ghost" size="icon-sm" />}
              aria-label={t("Close activity")}
            >
              <X />
            </DialogClose>
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
              <h3 className={styles.panelTitle}>{labels[key]}</h3>
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
              {key === "notes" && tab === key && (
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
                  {!guest && <ThreadBotNote threadId={threadId} />}
                </>
              )}
            </div>
          </div>
        </DialogPopup>
      </Dialog>
    </div>
  );
}
