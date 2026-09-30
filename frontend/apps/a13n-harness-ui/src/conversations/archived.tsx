import { useState } from "react";
import { Archive } from "@phosphor-icons/react";
import { Button, FormField, Input } from "a13n-ui";
import { PageHeader, ErrorNotice } from "../shell/ui";
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
    <section className={styles.page} aria-label="Archived conversations">
      <PageHeader
        title="Archived conversations"
        description="Conversations you've put away. Their history is kept, and you can restore them to the sidebar at any time."
      />
      <div className={styles.search}>
        <FormField label="Search archived conversations" hideLabel>
          <Input
            type="search"
            placeholder="Search archived conversations…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </FormField>
      </div>
      <ErrorNotice error={list.error} retry={() => void list.refetch()} />
      {list.isPending && <p role="status">Loading archived conversations…</p>}
      {list.isSuccess && !rows.length && (
        <div className={styles.empty}>
          <Archive size={32} aria-hidden="true" />
          <h2>
            {query.trim()
              ? "No matching conversations"
              : "No archived conversations"}
          </h2>
          <p>
            {query.trim()
              ? "Try a different search."
              : "Use a conversation's menu to archive it without deleting its history."}
          </p>
        </div>
      )}
      <div className={styles.rows}>
        {rows.map((row) => (
          <div key={row.thread.thread_id} className={styles.row}>
            <small>{row.project_name}</small>
            <ThreadRow row={row} showRestore />
          </div>
        ))}
      </div>
      {list.hasNextPage && (
        <Button
          variant="outline"
          loading={list.isFetchingNextPage}
          onClick={() => void list.fetchNextPage()}
        >
          Show more archived conversations
        </Button>
      )}
    </section>
  );
}
