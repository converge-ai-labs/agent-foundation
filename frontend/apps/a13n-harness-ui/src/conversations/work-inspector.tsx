import { useState } from "react";
import {
  Button,
  Popover,
  PopoverTrigger,
  PopoverPopup,
  PopoverTitle,
  PopoverDescription,
  PopoverClose,
  Skeleton,
} from "a13n-ui";
import {
  CheckCircle,
  Circle,
  CircleHalf,
  ListChecks,
  Notebook,
  UsersThree,
  Terminal,
  X,
} from "@phosphor-icons/react";
import { type Schema } from "../transport/client";
import { useThreadWork, workCaption } from "./work";
import { ErrorNotice } from "../shell/ui";
import { MessageText } from "./message-text";
import { Children } from "./details";
import type { FocusDisplay } from "./stream";
import { Processes } from "./processes";
import styles from "./work-inspector.module.css";

type Task = Schema<"TaskView">;
const rank = { in_progress: 0, pending: 1, completed: 2 };
export function orderedTasks(tasks: Task[]) {
  return tasks.slice().sort((a, b) => rank[a.status] - rank[b.status]);
}
function TaskIcon({ status }: { status: Task["status"] }) {
  const Icon =
    status === "completed"
      ? CheckCircle
      : status === "in_progress"
        ? CircleHalf
        : Circle;
  return (
    <Icon aria-label={status.replaceAll("_", " ")} className={styles[status]} />
  );
}
export function TaskList({ page }: { page?: Schema<"TaskPage"> }) {
  if (!page) return null;
  if (page.available === false)
    return <p>Task projection is unavailable for this provider.</p>;
  return (
    <>
      <ul className={styles.tasks}>
        {orderedTasks(page.tasks ?? []).map((task) => (
          <li key={task.task_id}>
            <TaskIcon status={task.status} />
            <details>
              <summary>
                {task.status === "in_progress"
                  ? task.active_form || task.subject
                  : task.subject}
              </summary>
              <p>{task.subject}</p>
              <dl>
                <div>
                  <dt>Task</dt>
                  <dd>{task.task_id}</dd>
                </div>
                <div>
                  <dt>Status</dt>
                  <dd>{task.status.replaceAll("_", " ")}</dd>
                </div>
                <div>
                  <dt>Owner</dt>
                  <dd>{task.owner || "Unassigned"}</dd>
                </div>
                {!!task.blocked_by?.length && (
                  <div>
                    <dt>Blocked by</dt>
                    <dd>{task.blocked_by.join(", ")}</dd>
                  </div>
                )}
                {!!task.blocks?.length && (
                  <div>
                    <dt>Blocks</dt>
                    <dd>{task.blocks.join(", ")}</dd>
                  </div>
                )}
              </dl>
            </details>
          </li>
        ))}
      </ul>
      {!page.tasks?.length && <p>No tasks yet.</p>}
      {!!page.omitted && <p>{page.omitted} tasks outside this bounded view.</p>}
    </>
  );
}
export function NoteList({ page }: { page?: Schema<"NotePage"> | null }) {
  if (!page) return null;
  return (
    <div className={styles.notes}>
      {page.notes?.map((note) => (
        <details key={note.key}>
          <summary>{note.key}</summary>
          <MessageText text={note.value} />
        </details>
      ))}
      {!page.notes?.length && <p>No notes yet.</p>}
      {!!page.omitted && <p>{page.omitted} notes omitted by the server.</p>}
    </div>
  );
}
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
}: {
  threadId: string;
  display: FocusDisplay;
  connected?: boolean;
  reconcile: () => void;
}) {
  const [tab, setTab] = useState<
    "tasks" | "notes" | "children" | "processes" | null
  >(null);
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
  };
  return (
    <div className={styles.strip} aria-label="Work inspection">
      {(["tasks", "notes", "children", "processes"] as const).map((key) => {
        const Icon =
          key === "tasks"
            ? ListChecks
            : key === "notes"
              ? Notebook
              : key === "children"
                ? UsersThree
                : Terminal;
        return (
          <Popover
            key={key}
            open={tab === key}
            onOpenChange={(open) => setTab(open ? key : null)}
          >
            <PopoverTrigger
              render={<Button variant="ghost" size="sm" />}
              aria-label={`Inspect ${labels[key].toLowerCase()}`}
              title={
                key === "processes"
                  ? undefined
                  : workCaption(
                      summary,
                      work.stale || !connected,
                      work.summary.isFetching,
                    )
              }
            >
              <Icon />
              {labels[key]}
              {key === "children" && !!childTotal && (
                <span className={styles.count}>{childTotal}</span>
              )}
              {key === "notes" && !!summary?.notes.total && (
                <span className={styles.count}>{summary.notes.total}</span>
              )}
              {key === "processes" && running > 0 && (
                <span className={styles.count}>
                  {running}
                  {incomplete || display.processes.omitted ? "+" : ""}
                </span>
              )}
              {key === "tasks" && !!total && (
                <span className={styles.count}>
                  {complete}/{total}
                </span>
              )}
            </PopoverTrigger>
            <PopoverPopup
              side="top"
              align="start"
              sideOffset={10}
              className={styles.popup}
            >
              <div className={styles.heading}>
                <PopoverTitle>{labels[key]}</PopoverTitle>
                <PopoverClose
                  render={<Button variant="ghost" size="icon-sm" />}
                  aria-label={`Close ${labels[key].toLowerCase()}`}
                >
                  <X />
                </PopoverClose>
              </div>
              <PopoverDescription className={styles.caption}>
                {key === "tasks"
                  ? "Current task state. Expand a task to inspect ownership and dependencies."
                  : key === "children"
                    ? "Inspect subordinate executions without leaving this conversation."
                    : key === "processes"
                      ? "Last observed background processes from this conversation and its subagents. Not a host process inventory."
                      : "Working context retained with this conversation."}
              </PopoverDescription>
              {(key === "tasks" || key === "notes") && (
                <p className={styles.caption}>
                  {workCaption(
                    key === "tasks" ? work.tasks.data : work.notes.data,
                    work.stale || !connected,
                    work.summary.isFetching ||
                      (key === "tasks"
                        ? work.tasks.isFetching
                        : work.notes.isFetching),
                  )}
                </p>
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
                  {!saved.isPending && !page && (
                    <p>Task observation unavailable.</p>
                  )}
                  <TaskList page={page} />
                </>
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
                    <p>Note observation unavailable.</p>
                  )}
                  <NoteList page={work.notes.data?.notes.page} />
                </>
              )}
              {key === "children" && tab === key && (
                <>
                  {summary && (
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
            </PopoverPopup>
          </Popover>
        );
      })}
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
