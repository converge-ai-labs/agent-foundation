import { useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { Button, Skeleton } from "a13n-ui";
import { ArrowUpRight, Notebook } from "@phosphor-icons/react";
import { useTranslation } from "./i18n";
import { useProjects, useTransport } from "./transport/context";
import { result, type Schema } from "./transport/client";
import { useBot } from "./bot";
import { MessageText } from "./conversations/message-text";
import { useThread } from "./conversations/queries";
import { ErrorNotice } from "./shell/ui";
import styles from "./bot-notes.module.css";

export function useBotNote(threadId: string, enabled = true) {
  const { client, shareId } = useTransport();
  return useQuery({
    queryKey: ["bot-note", threadId],
    enabled: enabled && !!threadId && !shareId,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/bot/notes/{thread_id}", {
          params: { path: { thread_id: threadId } },
          signal,
        }),
      ),
  });
}

function WorkNote({ note }: { note: Schema<"BotNoteSummary"> }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const full = useBotNote(note.thread_id, expanded);
  const projects = useProjects();
  const project = projects.data?.find(
    (item) => item.project_id === note.project_id,
  );
  return (
    <article className={styles.note}>
      <div className={styles.noteHeading}>
        <Link to={`/threads/${note.thread_id}`} className={styles.title}>
          {note.title || t("Untitled chat")}
          <ArrowUpRight aria-hidden="true" />
        </Link>
      </div>
      <div className={styles.metadata}>
        <span>{project?.name || note.project_id || t("No project")}</span>
        <time dateTime={note.updated_at}>
          {new Date(note.updated_at).toLocaleString()}
        </time>
      </div>
      <div
        className={expanded ? styles.full : styles.preview}
        aria-busy={expanded && full.isFetching}
      >
        <MessageText
          text={
            expanded && full.data ? (full.data.content ?? "") : note.summary
          }
        />
      </div>
      <ErrorNotice error={full.error} retry={() => void full.refetch()} />
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
      >
        {expanded ? t("Show less") : t("Read full note")}
      </Button>
    </article>
  );
}

export function BotWorkNotes() {
  const { t } = useTranslation();
  const { client } = useTransport();
  const notes = useInfiniteQuery({
    queryKey: ["bot-notes"],
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) =>
      result(
        client.GET("/api/bot/notes", {
          params: { query: { offset: pageParam, limit: 20 } },
          signal,
        }),
      ),
    getNextPageParam: (page) => page.next_offset ?? undefined,
  });
  const rows = notes.data?.pages.flatMap((page) => page.notes) ?? [];
  return (
    <section className={styles.panel}>
      <header className={styles.heading}>
        <h2>{t("Work notes")}</h2>
        <p>
          {t(
            "Agent-managed handoffs for ongoing work. Updates do not wake Bot.",
          )}
        </p>
      </header>
      <div className={`${styles.reader} a13n-scrollbar`}>
        <ErrorNotice error={notes.error} retry={() => void notes.refetch()} />
        {notes.isPending && <Skeleton className="h-16 w-full" />}
        {!notes.isPending && !notes.isError && !rows.length && (
          <div className={styles.emptyState}>
            <Notebook size={28} aria-hidden="true" />
            <p>{t("No work needs follow-up yet.")}</p>
          </div>
        )}
        {rows.map((note) => (
          <WorkNote key={note.thread_id} note={note} />
        ))}
        {notes.hasNextPage && (
          <Button
            variant="ghost"
            loading={notes.isFetchingNextPage}
            onClick={() => void notes.fetchNextPage()}
          >
            {t("Show more")}
          </Button>
        )}
      </div>
    </section>
  );
}

export function ThreadBotNote({
  threadId,
  hideEmpty = false,
}: {
  threadId: string;
  hideEmpty?: boolean;
}) {
  const { t } = useTranslation();
  const thread = useThread(threadId);
  const eligible =
    !!thread.data &&
    !thread.data.thread.parent_thread_id &&
    !thread.data.thread.memory_scope &&
    thread.data.thread.configuration.agent_source.kind === "agent";
  const note = useBotNote(threadId, eligible);
  const bot = useBot(eligible);
  if (!eligible || (hideEmpty && !note.data?.content && !note.error))
    return null;
  return (
    <section className={styles.own}>
      <div className={styles.ownHeading}>
        <h3>{t("Bot Note")}</h3>
        {bot.data && (
          <span>
            {!bot.data.enabled
              ? t("Bot is off")
              : bot.data.state.paused
                ? t("Automation paused")
                : t("Bot follow-up enabled")}
          </span>
        )}
      </div>
      <p className={styles.caption}>
        {t(
          "Shared handoff with Bot, separate from this chat's private working notes.",
        )}
      </p>
      <ErrorNotice error={note.error} retry={() => void note.refetch()} />
      {note.isPending ? (
        <Skeleton className="h-8 w-full" />
      ) : note.data?.content ? (
        <MessageText text={note.data.content} />
      ) : (
        <p className={styles.empty}>{t("No Bot Note.")}</p>
      )}
    </section>
  );
}
