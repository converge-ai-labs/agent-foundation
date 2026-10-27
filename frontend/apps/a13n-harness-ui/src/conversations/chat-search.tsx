import { useRef, useState } from "react";
import { Link } from "react-router";
import {
  Button,
  Dialog,
  DialogPopup,
  DialogTitle,
  DialogTrigger,
  Input,
  SearchPicker,
} from "a13n-ui";
import { MagnifyingGlass, ChatCircle } from "@phosphor-icons/react";
import { useProjects } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import { useThreads } from "./queries";
import { conversationTitle } from "./local-input";
import navigation from "./conversation.module.css";
import styles from "./chat-search.module.css";

export function ChatSearch({ onOpen }: { onOpen: () => void }) {
  const projects = useProjects();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const results = useRef<HTMLDivElement>(null);
  const close = () => {
    setOpen(false);
    setQuery("");
    setScope("");
  };
  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        setOpen(value);
        if (value) onOpen();
        if (!value) {
          setQuery("");
          setScope("");
        }
      }}
    >
      <DialogTrigger className={navigation.homeLink}>
        <MagnifyingGlass size={18} />
        <span>Search chats</span>
      </DialogTrigger>
      <DialogPopup
        placement="top"
        className={styles.popup}
        initialFocus={input}
        onKeyDown={(event) => {
          if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
          const links = [
            ...(results.current?.querySelectorAll<HTMLAnchorElement>("a") ??
              []),
          ];
          if (!links.length) return;
          const index = links.findIndex(
            (link) => link === document.activeElement,
          );
          if (document.activeElement !== input.current && index < 0) return;
          event.preventDefault();
          const next = index + (event.key === "ArrowDown" ? 1 : -1);
          if (next < 0) input.current?.focus();
          else links[Math.min(next, links.length - 1)]?.focus();
        }}
      >
        <DialogTitle className={navigation.srOnly}>Search chats</DialogTitle>
        <div className={styles.search}>
          <MagnifyingGlass size={20} aria-hidden="true" />
          <Input
            unstyled
            ref={input}
            aria-label="Find chats"
            type="search"
            placeholder="Search chats…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <div className={styles.scope}>
          <SearchPicker
            label="Project scope"
            placeholder="All projects"
            emptyMessage="No matching projects."
            value={scope}
            onValueChange={setScope}
            groups={[
              {
                label: "Projects",
                options: [
                  { value: "", label: "All projects" },
                  ...(projects.data ?? []).map((project) => ({
                    value: project.project_id,
                    label: project.name,
                  })),
                ],
              },
            ]}
          />
        </div>
        <div ref={results} className={`${styles.results} a13n-scrollbar`}>
          {open && query.trim() ? (
            <SearchResults query={query.trim()} scope={scope} close={close} />
          ) : (
            <p className={styles.empty}>Find a chat by title or message.</p>
          )}
        </div>
      </DialogPopup>
    </Dialog>
  );
}

function SearchResults({
  query,
  scope,
  close,
}: {
  query: string;
  scope: string;
  close: () => void;
}) {
  const list = useThreads(query, scope || undefined);
  const rows = [
    ...new Map(
      (list.data?.pages.flatMap((page) => page.rows) ?? []).map((row) => [
        row.thread.thread_id,
        row,
      ]),
    ).values(),
  ];
  return (
    <section aria-label="Chat search results">
      {rows.map((row) => (
        <Link
          key={row.thread.thread_id}
          to={`/threads/${encodeURIComponent(row.thread.thread_id)}`}
          className={styles.result}
          aria-label={conversationTitle(row.thread)}
          onClick={close}
        >
          <ChatCircle size={18} aria-hidden="true" />
          <span>
            <strong>{conversationTitle(row.thread)}</strong>
            <small>
              {row.project_name ?? "Without a project"}
              {row.thread.coordinator_thread_id ? " · Coordinator worker" : ""}
            </small>
          </span>
        </Link>
      ))}
      {list.isFetching && !list.isFetchingNextPage && (
        <p role="status" className={styles.empty}>
          {list.data ? "Updating results…" : "Searching chats…"}
        </p>
      )}
      {list.isSuccess && !rows.length && (
        <p className={styles.empty}>No matching chats.</p>
      )}
      <ErrorNotice error={list.error} retry={() => void list.refetch()} />
      {list.hasNextPage && (
        <Button
          variant="ghost"
          loading={list.isFetchingNextPage}
          onClick={() => void list.fetchNextPage()}
        >
          Show more results
        </Button>
      )}
    </section>
  );
}
