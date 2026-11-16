import { Button, ScrollArea } from "a13n-ui";
import { CaretLeft, ListChecks, Notebook } from "@phosphor-icons/react";
import { useTranslation } from "../i18n";
import { ThreadBotNote } from "../bot-notes";
import { useGuestAccess } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import { HelpTip } from "../shell/help-tip";
import { useDebug } from "../shell/debug";
import { ViewLink } from "../native/view-link";
import { useBackground } from "./background-context";
import {
  sectionTarget,
  SECTION_LABELS,
  type WorkSection,
} from "./background-work";
import { TaskIcon, orderedTasks } from "./work-lists";
import { useThreadWork, workCaption } from "./work";
import { MessageText } from "./message-text";
import { DebugId } from "../shell/debug";
import panel from "./work-tab.module.css";
import styles from "./background.module.css";

export function WorkPreview() {
  const { t } = useTranslation();
  const bg = useBackground();
  const work = useThreadWork(bg.threadId, { tasks: true, notes: true });
  return (["tasks", "notes"] as const).map((section) => {
    const target = sectionTarget(section, bg.threadId, t);
    const data = work[section].data;
    const tasks = data?.tasks.page?.tasks ?? [];
    const notes = data?.notes.page?.notes ?? [];
    const rows =
      section === "tasks"
        ? orderedTasks(tasks)
            .slice(0, 3)
            .map((task) => ({
              id: task.task_id,
              label:
                task.status === "in_progress"
                  ? task.active_form || task.subject
                  : task.subject,
              icon: <TaskIcon status={task.status} />,
            }))
        : notes.slice(0, 3).map((note) => ({
            id: note.key,
            label: note.key,
            icon: <Notebook />,
          }));
    return (
      <section key={section} aria-label={target.title}>
        <div className={styles.groupHeader}>
          <h3>{target.title}</h3>
          <Button
            variant="ghost"
            size="sm"
            aria-label={t("View all") + " · " + target.title}
            onClick={() => bg.open(target)}
          >
            {t("View all")}
          </Button>
        </div>
        {rows.length ? (
          <ul>
            {rows.map((row) => (
              <li className={styles.row} key={row.id}>
                <button
                  className={styles.item}
                  onClick={() => bg.open({ ...target, selection: row.id })}
                >
                  <span className={styles.icon}>{row.icon}</span>
                  <span className={styles.name}>{row.label}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className={styles.previewEmpty}>
            {work[section].isPending
              ? t(section === "tasks" ? "Loading tasks" : "Loading notes")
              : !data?.[section]?.available || work[section].error
                ? t(
                    section === "tasks"
                      ? "Task state unavailable."
                      : "Notes unavailable.",
                  )
                : t(section === "tasks" ? "No tasks yet." : "No notes yet.")}
          </p>
        )}
      </section>
    );
  });
}

export function WorkPanel({
  section,
  threadId,
  selection,
}: {
  section: WorkSection;
  threadId: string;
  selection?: string;
}) {
  const { t } = useTranslation();
  const guest = useGuestAccess();
  const debug = useDebug();
  const work = useThreadWork(threadId, { [section]: true });
  const query = work[section];
  const bg = useBackground();
  const target = sectionTarget(section, threadId, t);
  const select = (item?: string) => bg.open({ ...target, selection: item });
  const data = query.data?.[section];
  const tasks = query.data?.tasks.page?.tasks ?? [];
  const notes = query.data?.notes.page?.notes ?? [];
  const task =
    section === "tasks"
      ? tasks.find((item) => item.task_id === selection)
      : undefined;
  const note =
    section === "notes"
      ? notes.find((item) => item.key === selection)
      : undefined;
  const statusLabels = {
    in_progress: t("In progress"),
    pending: t("Pending"),
    completed: t("Completed"),
  };
  const Icon = section === "tasks" ? ListChecks : Notebook;
  const label = t(SECTION_LABELS[section]);
  return (
    <section className={styles.panel} aria-label={label}>
      <header className={styles.panelHeader}>
        {task || note ? (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t("Back")}
            onClick={() => select()}
          >
            <CaretLeft />
          </Button>
        ) : (
          <Icon size={18} />
        )}
        <div className={styles.panelIdentity}>
          <h2>{label}</h2>
        </div>
        <HelpTip label={t("About activity")}>
          {workCaption(
            t,
            query.data,
            work.stale,
            query.isFetching,
            debug.enabled,
          )}
        </HelpTip>
        <ViewLink
          getLink={() =>
            new URL(
              "/threads/" + encodeURIComponent(threadId) + "?native=" + section,
              window.location.origin,
            ).href
          }
        />
      </header>
      <ScrollArea className={styles.workBody} scrollFade>
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
        {query.isPending ? (
          <p role="status">
            {t(section === "tasks" ? "Loading tasks" : "Loading notes")}
          </p>
        ) : !query.data?.[section]?.page && !query.error ? (
          <p>
            {t(
              section === "tasks"
                ? "Task state unavailable."
                : "Notes unavailable.",
            )}
          </p>
        ) : null}
        {task ? (
          <article className={panel.reader}>
            <h3>{task.subject}</h3>
            <p className={panel.status}>
              <TaskIcon status={task.status} />
              {statusLabels[task.status]}
            </p>
            {task.status === "in_progress" &&
              task.active_form &&
              task.active_form !== task.subject && <p>{task.active_form}</p>}
            <dl className={panel.facts}>
              <dt>{t("Owner")}</dt>
              <dd>{task.owner || t("Unassigned")}</dd>
              {(
                [
                  [t("Blocked by"), task.blocked_by],
                  [t("Blocks"), task.blocks],
                ] as const
              ).map(
                ([title, ids]) =>
                  !!ids?.length && (
                    <div key={String(title)}>
                      <dt>{title}</dt>
                      <dd>
                        {ids.map((id) => {
                          const related = tasks.find(
                            (item) => item.task_id === id,
                          );
                          return related ? (
                            <button key={id} onClick={() => select(id)}>
                              {related.subject}
                            </button>
                          ) : (
                            <span key={id}>
                              {t("Task outside this list")}
                              <DebugId label={t("Task")} value={id} />
                            </span>
                          );
                        })}
                      </dd>
                    </div>
                  ),
              )}
            </dl>
            <DebugId label={t("Task")} value={task.task_id} />
          </article>
        ) : note ? (
          <article className={panel.reader}>
            <h3>{note.key}</h3>
            <MessageText text={note.value} />
          </article>
        ) : (
          <>
            {section === "tasks" &&
              (["in_progress", "pending", "completed"] as const).map(
                (status) => {
                  const items = tasks.filter((item) => item.status === status);
                  return (
                    items.length > 0 && (
                      <section key={status} className={panel.group}>
                        <h3>
                          {statusLabels[status]} <span>{items.length}</span>
                        </h3>
                        {items.map((item) => (
                          <button
                            key={item.task_id}
                            className={panel.task}
                            onClick={() => select(item.task_id)}
                          >
                            <TaskIcon status={status} />
                            <span>{item.subject}</span>
                          </button>
                        ))}
                      </section>
                    )
                  );
                },
              )}
            {section === "notes" &&
              notes.map((item) => (
                <button
                  className={panel.note}
                  key={item.key}
                  onClick={() => select(item.key)}
                >
                  <strong>{item.key}</strong>
                  <span>{item.value}</span>
                </button>
              ))}
            {data?.page && !(section === "tasks" ? tasks : notes).length && (
              <p className={panel.muted}>
                {data.available === false ||
                (section === "tasks" &&
                  query.data?.tasks.page?.available === false)
                  ? t(
                      section === "tasks"
                        ? "Task state unavailable."
                        : "Notes unavailable.",
                    )
                  : t(section === "tasks" ? "No tasks yet." : "No notes yet.")}
              </p>
            )}
            {!!data?.page?.omitted && (
              <p className={panel.muted}>
                {t(
                  section === "tasks"
                    ? "{{count}} tasks outside this bounded view."
                    : "{{count}} notes omitted by the server.",
                  { count: data.page.omitted },
                )}
              </p>
            )}
            {section === "notes" && !guest && (
              <ThreadBotNote threadId={threadId} hideEmpty />
            )}
          </>
        )}
      </ScrollArea>
    </section>
  );
}
