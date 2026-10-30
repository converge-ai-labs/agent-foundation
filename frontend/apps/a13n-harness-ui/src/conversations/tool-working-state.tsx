import { describeTool, record, type ToolView } from "./tool-presentation";
import styles from "./tool-call.module.css";

/** Readable recorded notes and tasks; input alone never implies success. */
export function ToolWorkingState({ tool }: { tool: ToolView }) {
  const { args, result } = describeTool(tool);
  if (tool.name === "note_write" || tool.name === "note_get") {
    const value = tool.name === "note_write" ? args.value : result.value;
    if (typeof value === "string")
      return <p className={styles.recordedText}>{value || "Empty note."}</p>;
    if (Array.isArray(result.keys))
      return result.keys.length ? (
        <ul className={styles.resources} aria-label="Notes">
          {result.keys
            .filter((key): key is string => typeof key === "string")
            .map((key) => (
              <li key={key}>{key}</li>
            ))}
        </ul>
      ) : (
        <p className={styles.recordedText}>No notes.</p>
      );
  }
  if (tool.name === "note_delete" && result.ok === true)
    return (
      <p className={styles.recordedText}>
        {result.action === "already_absent"
          ? "Note already removed."
          : "Note removed."}
      </p>
    );
  if (
    ["task_create", "task_update", "task_get", "task_list"].includes(tool.name)
  ) {
    const tasks = Array.isArray(result.tasks)
      ? result.tasks.filter(record)
      : record(result.task)
        ? [result.task]
        : [args];
    if (!tasks.length) return <p className={styles.recordedText}>No tasks.</p>;
    return (
      <ul className={styles.recordedTasks} aria-label="Recorded tasks">
        {tasks.map((task, index) => (
          <li key={index}>
            <div>
              {typeof task.subject === "string" && (
                <strong>{task.subject}</strong>
              )}
              {result.ok === true && typeof task.status === "string" && (
                <span>{task.status.replaceAll("_", " ")}</span>
              )}
            </div>
            {typeof task.description === "string" && <p>{task.description}</p>}
          </li>
        ))}
      </ul>
    );
  }
  return null;
}
