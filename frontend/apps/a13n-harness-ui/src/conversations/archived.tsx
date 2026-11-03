import { useTranslation } from "../i18n";
import { useState } from "react";
import { Button } from "a13n-ui";
import { PageHeader, ErrorNotice, SearchField } from "../shell/ui";
import { useThreads } from "./queries";
import { ThreadRow } from "./thread-row";
import styles from "./archived.module.css";

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
          <div key={row.thread.thread_id} className={styles.row}>
            <ThreadRow row={row} showRestore showProject />
          </div>
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
