import { CheckCircle, Circle, CircleHalf } from "@phosphor-icons/react";
import type { Schema } from "../transport/client";
import { MessageText } from "./message-text";
import { DebugOnly, DebugId } from "../shell/debug";
import styles from "./work-inspector.module.css";
import layout from "../shell/workbench.module.css";

type Task = Schema<"TaskView">;
const rank = { in_progress: 0, pending: 1, completed: 2 };
export function orderedTasks(tasks: Task[]) {
  return tasks.slice().sort((a, b) => rank[a.status] - rank[b.status]);
}
export function TaskIcon({ status }: { status: Task["status"] }) {
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
  const names = new Map(
    page.tasks?.map((task) => [task.task_id, task.subject]),
  );
  const dependencies = (ids: string[]) => (
    <div className={styles.dependencies}>
      {ids.map((id) => (
        <span key={id}>
          {names.get(id) ?? "Task outside this list"}
          <DebugId label="Task" value={id} />
        </span>
      ))}
    </div>
  );
  return (
    <>
      <ul className={styles.tasks}>
        {orderedTasks(page.tasks ?? []).map((task) => (
          <li key={task.task_id}>
            <TaskIcon status={task.status} />
            <details className={layout.details}>
              <summary>
                {task.status === "in_progress"
                  ? task.active_form || task.subject
                  : task.subject}
              </summary>
              {task.status === "in_progress" &&
                task.active_form &&
                task.active_form !== task.subject && <p>{task.subject}</p>}
              <dl>
                <DebugOnly>
                  <div>
                    <dt>Task</dt>
                    <dd>{task.task_id}</dd>
                  </div>
                </DebugOnly>
                <div>
                  <dt>Status</dt>
                  <dd>
                    {
                      {
                        in_progress: "In progress",
                        pending: "Pending",
                        completed: "Completed",
                      }[task.status]
                    }
                  </dd>
                </div>
                <div>
                  <dt>Owner</dt>
                  <dd>{task.owner || "Unassigned"}</dd>
                </div>
                {!!task.blocked_by?.length && (
                  <div>
                    <dt>Blocked by</dt>
                    <dd>{dependencies(task.blocked_by)}</dd>
                  </div>
                )}
                {!!task.blocks?.length && (
                  <div>
                    <dt>Blocks</dt>
                    <dd>{dependencies(task.blocks)}</dd>
                  </div>
                )}
              </dl>
            </details>
          </li>
        ))}
      </ul>
      {!page.tasks?.length && (
        <p className={styles.emptyState}>No tasks yet.</p>
      )}
      {!!page.omitted && <p>{page.omitted} tasks outside this bounded view.</p>}
    </>
  );
}
export function NoteList({ page }: { page?: Schema<"NotePage"> | null }) {
  if (!page) return null;
  return (
    <div className={styles.notes}>
      {page.notes?.map((note) => (
        <details key={note.key} className={layout.details}>
          <summary>{note.key}</summary>
          <MessageText text={note.value} />
        </details>
      ))}
      {!page.notes?.length && (
        <p className={styles.emptyState}>No notes yet.</p>
      )}
      {!!page.omitted && <p>{page.omitted} notes omitted by the server.</p>}
    </div>
  );
}
