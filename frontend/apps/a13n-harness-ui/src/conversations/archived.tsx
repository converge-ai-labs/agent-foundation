import { useTranslation } from "../i18n";
import { useState, useContext, type ReactNode } from "react";
import { Button } from "a13n-ui";
import { PageHeader, ErrorNotice, SearchField } from "../shell/ui";
import { useThreads } from "./queries";
import { Link } from "react-router";
import type { Schema } from "../transport/client";
import { ComposerDrafts } from "./composer";
import { DeleteThread, canDeleteThread } from "./delete-thread";
import { conversationTitle } from "./local-input";
import { useThreadArchive } from "./use-thread-archive";
import styles from "./archived.module.css";

function ThreadListItem({
  thread,
  title,
  project,
  actions,
}: {
  thread: Schema<"ThreadSummary">;
  title: string;
  project: string;
  actions: ReactNode;
}) {
  return (
    <div className={styles.item}>
      <Link
        className={styles.link}
        to={`/threads/${encodeURIComponent(thread.thread_id)}`}
      >
        <strong title={title}>{title}</strong>
        <small>{project}</small>
      </Link>
      <div className={styles.actions}>{actions}</div>
    </div>
  );
}

function ArchivedThreadItem({ row }: { row: Schema<"ThreadActivityView"> }) {
  const { t } = useTranslation();
  const { thread } = row;
  const composers = useContext(ComposerDrafts);
  const title = conversationTitle(
    thread,
    composers.get(thread.thread_id)?.localInputs,
    t("Untitled chat"),
  );
  const restore = useThreadArchive(thread);
  const [deleting, setDeleting] = useState(false);
  const disabled =
    thread.root_activity.state !== "inactive" || restore.isPending;
  return (
    <div className={styles.row}>
      <ThreadListItem
        thread={thread}
        title={title}
        project={
          row.project_name ??
          (thread.configuration.project_id
            ? t("Unavailable project")
            : t("Without a project"))
        }
        actions={
          <>
            <Button
              variant="secondary"
              size="xs"
              aria-label={t("Restore {{title}}", { title })}
              loading={restore.isPending}
              disabled={disabled}
              onClick={() => restore.mutate(false)}
            >
              {t("Restore")}
            </Button>
            {canDeleteThread(thread) && (
              <Button
                variant="destructive-outline"
                size="xs"
                aria-label={t("Delete {{title}}", { title })}
                disabled={disabled}
                onClick={() => setDeleting(true)}
              >
                {t("Delete")}
              </Button>
            )}
          </>
        }
      />
      <ErrorNotice error={restore.error} />
      {deleting && (
        <DeleteThread thread={thread} close={() => setDeleting(false)} />
      )}
    </div>
  );
}

export function ArchivedPage() {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const list = useThreads(query.trim(), undefined, false, {
    archivedOnly: true,
  });
  const rows = [
    ...new Map(
      (list.data?.pages.flatMap((page) => page.rows) ?? []).map((row) => [
        row.thread.thread_id,
        row,
      ]),
    ).values(),
  ];
  return (
    <section className={styles.page} aria-label={t("Archived chats")}>
      <PageHeader
        title={t("Archived chats")}
        description={t("Saved history you can restore to the sidebar.")}
      />
      <SearchField
        label={t("Search archived chats")}
        placeholder={t("Search archived chats…")}
        value={query}
        onChange={setQuery}
      />
      <ErrorNotice error={list.error} retry={() => void list.refetch()} />
      {list.isPending && <p role="status">{t("Loading archived chats…")}</p>}
      {list.isSuccess && !rows.length && (
        <div className={styles.empty}>
          <h2>
            {query.trim() ? t("No matching chats") : t("No archived chats")}
          </h2>
          <p>
            {query.trim()
              ? t("Try a different search.")
              : t(
                  "Use a chat's menu to archive it without deleting its history.",
                )}
          </p>
        </div>
      )}
      <div className={styles.rows}>
        {rows.map((row) => (
          <ArchivedThreadItem key={row.thread.thread_id} row={row} />
        ))}
      </div>
      {list.hasNextPage && (
        <Button
          variant="outline"
          loading={list.isFetchingNextPage}
          onClick={() => void list.fetchNextPage()}
        >
          {t("Load more")}
        </Button>
      )}
    </section>
  );
}
