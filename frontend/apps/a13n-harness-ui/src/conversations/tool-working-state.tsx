import conversation from "./conversation.module.css";
import { ScrollArea } from "a13n-ui";
import { useTranslation } from "../i18n";
import { describeTool, record, type ToolView } from "./tool-presentation";
import styles from "./tool-call.module.css";

/** Readable recorded notes and tasks; input alone never implies success. */
export function ToolWorkingState({ tool }: { tool: ToolView }) {
  const { t } = useTranslation();
  const { args, result } = describeTool(tool);
  if (tool.name === "note_write" || tool.name === "note_get") {
    const value = tool.name === "note_write" ? args.value : result.value;
    if (typeof value === "string")
      return <p className={styles.recordedText}>{value || t("Empty note.")}</p>;
    if (Array.isArray(result.keys))
      return result.keys.length ? (
        <ScrollArea
          autoHeight
          scrollbarGutter
          className={`${styles.resourceScroll} ${conversation.innerReader}`}
          data-tool-resources
        >
          <ul className={styles.resources} aria-label={t("Notes")}>
            {result.keys
              .filter((key): key is string => typeof key === "string")
              .map((key) => (
                <li key={key}>{key}</li>
              ))}
          </ul>
        </ScrollArea>
      ) : (
        <p className={styles.recordedText}>{t("No notes.")}</p>
      );
  }
  if (tool.name === "note_delete" && result.ok === true)
    return (
      <p className={styles.recordedText}>
        {result.action === "already_absent"
          ? t("Note already removed.")
          : t("Note removed.")}
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
    if (!tasks.length)
      return <p className={styles.recordedText}>{t("No tasks.")}</p>;
    return (
      <ul className={styles.recordedTasks} aria-label={t("Recorded tasks")}>
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
