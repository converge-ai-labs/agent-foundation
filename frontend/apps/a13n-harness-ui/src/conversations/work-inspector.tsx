import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
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
import { result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import { MessageText } from "./message-text";
import { Children, useChildExecutions } from "./details";
import type { FocusDisplay } from "./stream";
import { Processes } from "./processes";
import styles from "./work-inspector.module.css";

type Task = Schema<"TaskView">;
const rank = { in_progress: 0, pending: 1, completed: 2 };
export function orderedTasks(tasks: Task[]) {
  return tasks.slice().sort((a, b) => rank[a.status] - rank[b.status]);
}
function taskProjection(
  saved?: Schema<"TaskPage">,
  observed?: Schema<"TaskPage">,
) {
  if (!observed || (saved?.version ?? -1) > (observed.version ?? -1))
    return saved;
  // Focus carries deltas, not an initial task directory. Preserve unchanged
  // saved tasks without letting an older HTTP response replace live updates.
  const tasks = new Map(
    (saved?.tasks ?? []).map((task) => [task.task_id, task]),
  );
  for (const task of observed.tasks ?? []) {
    if (task.version >= (tasks.get(task.task_id)?.version ?? -1))
      tasks.set(task.task_id, task);
  }
  return {
    ...saved,
    ...observed,
    tasks: [...tasks.values()].slice(-100),
    omitted:
      Math.max(saved?.omitted ?? 0, observed.omitted ?? 0) +
      Math.max(0, tasks.size - 100),
  };
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
export function SavedNotes({
  threadId,
  continuation,
}: {
  threadId: string;
  continuation?: string | null;
}) {
  const { client } = useTransport();
  const notes = useQuery({
    queryKey: ["thread", threadId, "notes", continuation],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/notes", {
          params: {
            path: { thread_id: threadId },
            query: { expected_continuation_id: continuation ?? undefined },
          },
          signal,
        }),
      ),
  });
  return (
    <div className={styles.notes}>
      <p className={styles.caption}>
        Saved continuation notes. Unsaved changes may not appear here yet.
      </p>
      <ErrorNotice error={notes.error} retry={() => void notes.refetch()} />
      {notes.isPending && <Loading label="Loading saved notes" />}
      {notes.data?.notes?.map((note) => (
        <details key={note.key}>
          <summary>{note.key}</summary>
          <MessageText text={note.value} />
        </details>
      ))}
      {notes.data && !notes.data.notes?.length && <p>No saved notes.</p>}
      {!!notes.data?.omitted && (
        <p>{notes.data.omitted} notes omitted by the server.</p>
      )}
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
  continuation,
  display,
  live,
  connected = true,
  reconcile,
}: {
  threadId: string;
  continuation?: string | null;
  display: FocusDisplay;
  live: boolean;
  connected?: boolean;
  reconcile: () => void;
}) {
  const { client } = useTransport();
  const [tab, setTab] = useState<
    "tasks" | "notes" | "children" | "processes" | null
  >(null);
  const saved = useQuery({
    queryKey: ["thread", threadId, "tasks", continuation],
    enabled: tab === "tasks",
    staleTime: Infinity,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/tasks", {
          params: {
            path: { thread_id: threadId },
            query: {
              expected_continuation_id: continuation ?? undefined,
              limit: 100,
            },
          },
          signal,
        }),
      ),
  });
  const children = useChildExecutions(threadId, tab === "children");
  const current = display.tasks;
  const page = taskProjection(
    saved.data,
    current && (live || display.baseContinuation === continuation)
      ? current
      : undefined,
  );
  const tasks = orderedTasks(page?.tasks ?? []);
  const active = tasks.find((task) => task.status === "in_progress");
  const complete = tasks.filter((task) => task.status === "completed").length;
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
            >
              <Icon />
              {labels[key]}
              {key === "children" && !!children.data?.pages[0].total && (
                <span className={styles.count}>
                  {children.data.pages[0].total}
                </span>
              )}
              {key === "processes" && running > 0 && (
                <span className={styles.count}>
                  {running}
                  {incomplete || display.processes.omitted ? "+" : ""}
                </span>
              )}
              {key === "tasks" && !!tasks.length && (
                <span className={styles.count}>
                  {complete}/{tasks.length}
                  {page?.omitted ? "+" : ""}
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
              {key === "tasks" && (
                <>
                  <ErrorNotice
                    error={saved.error}
                    retry={() => void saved.refetch()}
                  />
                  {!page && saved.isPending && (
                    <Loading label="Loading tasks" />
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
                <SavedNotes threadId={threadId} continuation={continuation} />
              )}
              {key === "children" && tab === key && (
                <Children
                  threadId={threadId}
                  reconcile={reconcile}
                  display={display}
                />
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
