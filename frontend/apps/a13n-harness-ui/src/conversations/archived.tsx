import { useState } from "react";
import { Button } from "a13n-ui";
import { PageHeader, ErrorNotice, SearchField } from "../shell/ui";
import { useThreads } from "./queries";
import { ThreadRow } from "./thread-row";
import styles from "./archived.module.css";

export function ArchivedPage() {
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
    <section className={styles.page} aria-label="Archived chats">
      <PageHeader
        title="Archived chats"
        description="Saved history you can restore to the sidebar."
      />
      <SearchField
        label="Search archived chats"
        value={query}
        onChange={setQuery}
      />
      <ErrorNotice error={list.error} retry={() => void list.refetch()} />
      {list.isPending && <p role="status">Loading archived chats…</p>}
      {list.isSuccess && !rows.length && (
        <div className={styles.empty}>
          <h2>{query.trim() ? "No matching chats" : "No archived chats"}</h2>
          <p>
            {query.trim()
              ? "Try a different search."
              : "Use a chat's menu to archive it without deleting its history."}
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
          Load more
        </Button>
      )}
    </section>
  );
}
